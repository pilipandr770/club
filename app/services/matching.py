"""Матчинг участников.

score(A→B) = 0.6·cos(need_A, offer_B) + 0.4·cos(need_B, offer_A)   (CLAUDE.md, раздел «Матчинг»)

Нужда — задача и поиск. Предложение — чем человек занимается и может помочь.
Участникам показываем только метку (например «хорошо подходит»), сырые значения — только администратору.
Контакты здесь не возвращаются никогда.
"""

import hashlib
import json
import logging
import math

from flask import current_app
from flask_babel import get_locale, gettext as _, lazy_gettext as _l

log = logging.getLogger(__name__)
from ..extensions import db
from ..models import Match, Profile, User
from ..profile_guide import matchable
from ..utils import utcnow
from .ai import AIUnavailable, ask, get_embedder, llm_enabled
from .ai.embeddings import LexicalEmbedder

WEIGHT_NEED_TO_OFFER = 0.6
WEIGHT_REVERSE = 0.4
DIM = 512
TOP_K = 5
LABELS = ((0.45, _l("Good fit")), (0.25, _l("Partial fit")), (0.0, _l("Little overlap")))


# --- тексты и версии -----------------------------------------------------------

def need_text(profile) -> str:
    return "\n".join(part for part in (profile.q_challenge, profile.q_looking_for) if part and part.strip())


def offer_text(profile) -> str:
    parts = (profile.headline, profile.industry, profile.q_focus, profile.q_can_help, profile.expertise, profile.bio)
    return "\n".join(part for part in parts if part and part.strip())


def profile_version(profile) -> str:
    """Хеш текстов: меняется при любой правке, влияющей на матчинг."""
    raw = need_text(profile) + "\x1f" + offer_text(profile)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- провайдер эмбеддингов ----------------------------------------------------

def _embedder():
    """Провайдер из настроек. Если он не настроен, честно падаем на лексический и пишем это в лог."""
    try:
        return get_embedder()
    except AIUnavailable as exc:
        log.warning("Эмбеддинги не настроены (%s), используется лексический режим", exc)
        return LexicalEmbedder()


def refresh_embeddings(profile, embedder=None) -> bool:
    """Пересчитывает эмбеддинги, если текст или провайдер изменились. Возвращает True, если пересчитали."""
    embedder = embedder or _embedder()
    version = profile_version(profile)
    if profile.embed_hash == version and profile.embed_provider == embedder.name and profile.embed_need:
        return False
    need, offer = embedder.embed([need_text(profile), offer_text(profile)])
    profile.embed_need = need
    profile.embed_offer = offer
    profile.embed_hash = version
    profile.embed_provider = embedder.name
    profile.embed_updated_at = utcnow()
    return True


# --- оценка --------------------------------------------------------------------

def cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


# Плотные модели (mistral-embed и др.) дают косинус 0,7-0,8 даже для несвязанных текстов:
# без поправки все пары попали бы в «хорошее совпадение». Поправка сдвигает «пол» к нулю.
DENSE_FLOOR = 0.65


def _calibrated(profile, value: float) -> float:
    if (profile.embed_provider or "").startswith("lexical"):
        return value
    if not profile.embed_provider:
        return value
    return min(1.0, max(0.0, (value - DENSE_FLOOR) / (1.0 - DENSE_FLOOR)))


def score(profile_a, profile_b) -> float:
    need_to_offer = _calibrated(profile_a, cosine(profile_a.embed_need, profile_b.embed_offer))
    reverse = _calibrated(profile_b, cosine(profile_b.embed_need, profile_a.embed_offer))
    return WEIGHT_NEED_TO_OFFER * need_to_offer + WEIGHT_REVERSE * reverse


def fit_label(value: float) -> str:
    for threshold, label in LABELS:
        if value >= threshold:
            return str(label)
    return str(LABELS[-1][1])


# --- подбор ----------------------------------------------------------------------

def candidates(user):
    """Участники с согласием на матчинг, проверенные, с эмбеддингами той же модели, что и у меня.

    Профили, посчитанные другой моделью, в сравнение не попадают: векторы из разных моделей несравнимы.
    """
    name = _embedder().name
    return (db.session.query(User, Profile)
            .join(Profile, Profile.user_id == User.id)
            .filter(User.id != user.id,
                    User.status == "active",
                    User.verification_status == "verified",
                    Profile.allow_matching.is_(True),
                    Profile.embed_need.isnot(None),
                    Profile.embed_provider == name)
            .all())


def top_matches(user, k: int = TOP_K):
    """Лучшие пары для участника: [(User, score)], по убыванию. Пусто, если его профиль не пригоден."""
    profile = user.profile
    if profile is None or not matchable(profile):
        return []
    refresh_embeddings(profile)
    scored = []
    for other, other_profile in candidates(user):
        if not matchable(other_profile):
            continue
        scored.append((other, other_profile, score(profile, other_profile)))
    scored.sort(key=lambda item: item[2], reverse=True)
    return [(other, value) for other, _, value in scored[:k]]


# --- объяснения ----------------------------------------------------------------

def _template_text(profile, other_profile) -> str:
    need = (profile.q_looking_for or profile.q_challenge or "").strip()[:160]
    offer = (other_profile.q_can_help or other_profile.q_focus or "").strip()[:160]
    return _("Need: %(need)s — offer: %(offer)s.", need=need, offer=offer)


def _llm_explanations(profile, others):
    """Один пакетный вызов на всех кандидатов. Имена и контакты в запрос не попадают."""
    lines = []
    for index, other_profile in enumerate(others, start=1):
        lines.append(f"Kandidat {index}: {offer_text(other_profile)[:600]}")
    user_text = (
        f"Gesuch der Person:\n{need_text(profile)[:800]}\n\n"
        f"Angebote der Kandidaten:\n" + "\n\n".join(lines) + "\n\n"
        "Schreibe für jeden Kandidaten einen kurzen Satz, warum er zum Gesuch passt. "
        "Antworte nur mit JSON: {\"1\": \"...\", \"2\": \"...\"}."
    )
    system = ("Du erklärst Mitgliedern eines Clubs kurz, warum zwei Profile zusammenpassen. "
              "Sei konkret und sachlich, ohne Werbung. Keine Namen, keine Kontaktdaten, keine Bewertungen der Person.")
    language = "Russian" if str(get_locale()) == "ru" else "German"
    raw = ask(system + f" Write all explanation text in {language}. Keep the JSON keys unchanged.", user_text)
    start, end = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[start:end + 1])
    return {index: str(data[str(index)]).strip()[:400] for index in range(1, len(others) + 1)
            if str(index) in data and str(data[str(index)]).strip()}


def get_matches(user, k: int = TOP_K):
    """Возвращает карточки совпадений для показа. Пересчитывает объяснения только при необходимости."""
    pairs = top_matches(user, k)
    if not pairs:
        return []
    my_profile = user.profile
    # Включаем язык в существующий хеш: кэш не отдаёт объяснение на прежнем языке.
    my_version = hashlib.sha256((profile_version(my_profile) + ":" + str(get_locale())).encode()).hexdigest()
    results = []
    stale = []
    for other, value in pairs:
        cached = Match.query.filter_by(user_id=user.id, other_id=other.id).first()
        other_version = profile_version(other.profile)
        fresh = (cached is not None and cached.version_a == my_version and cached.version_b == other_version
                 and (cached.explanation_source == "llm" or not llm_enabled()))
        if not fresh:
            stale.append((other, value, cached, other_version))
        else:
            results.append((other, fit_label(value), cached.explanation))

    if stale:
        texts = {}
        try:
            texts = _llm_explanations(my_profile, [other.profile for other, _, _, _ in stale])
            source = "llm"
        except (AIUnavailable, ValueError, KeyError, TypeError):
            source = "template"
        for index, (other, value, cached, other_version) in enumerate(stale, start=1):
            if source == "llm" and index in texts:
                text, used = texts[index], "llm"
            else:
                text, used = _template_text(my_profile, other.profile), "template"
            if cached is None:
                cached = Match(user_id=user.id, other_id=other.id)
                db.session.add(cached)
            cached.score = value
            cached.explanation = text
            cached.explanation_source = used
            cached.version_a = my_version
            cached.version_b = other_version
            cached.created_at = utcnow()
            results.append((other, fit_label(value), text))
        db.session.commit()

    order = {other.id: position for position, (other, _) in enumerate(pairs)}
    results.sort(key=lambda item: order[item[0].id])
    return results
