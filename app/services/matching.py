"""Матчинг участников.

score(A→B) = 0.6·cos(need_A, offer_B) + 0.4·cos(need_B, offer_A)   (CLAUDE.md, раздел «Матчинг»)

Нужда — задача и поиск. Предложение — чем человек занимается и может помочь.
Участникам показываем только метку (например «хорошо подходит»), сырые значения — только администратору.
Контакты здесь не возвращаются никогда.
"""

import hashlib
import json
import math
import re

from flask import current_app
from ..extensions import db
from ..models import Match, Profile, User
from ..profile_guide import matchable
from ..utils import utcnow
from .llm import LLMUnavailable, ask

WEIGHT_NEED_TO_OFFER = 0.6
WEIGHT_REVERSE = 0.4
DIM = 512
TOP_K = 5
LABELS = ((0.45, "gut passend"), (0.25, "teilweise passend"), (0.0, "wenig Überschneidung"))


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


# --- провайдеры эмбеддингов ---------------------------------------------------

class LexicalEmbedder:
    """Слова с хешированием (знаковый «hashing trick»). Детерминированный, без внешних сервисов.

    Видит совпадения слов, но не смысл. Годится для разработки и тестов; для боевого матчинга
    нужен семантический провайдер (решение владельцев: ARCHITECTURE.md §7, пункт 6).
    """

    name = "lexical-v1"

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def _vector(self, text):
        vector = [0.0] * DIM
        for token in re.findall(r"\w+", text.lower()):
            if len(token) < 3:
                continue
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def get_embedder():
    return LexicalEmbedder()


def refresh_embeddings(profile, embedder=None) -> bool:
    """Пересчитывает эмбеддинги, если текст или провайдер изменились. Возвращает True, если пересчитали."""
    embedder = embedder or get_embedder()
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


def score(profile_a, profile_b) -> float:
    return (WEIGHT_NEED_TO_OFFER * cosine(profile_a.embed_need, profile_b.embed_offer)
            + WEIGHT_REVERSE * cosine(profile_b.embed_need, profile_a.embed_offer))


def fit_label(value: float) -> str:
    for threshold, label in LABELS:
        if value >= threshold:
            return label
    return LABELS[-1][1]


# --- подбор ----------------------------------------------------------------------

def candidates(user):
    """Участники, которые дали согласие на матчинг и проверены. Сам пользователь и заблокированные исключены."""
    return (db.session.query(User, Profile)
            .join(Profile, Profile.user_id == User.id)
            .filter(User.id != user.id,
                    User.status == "active",
                    User.verification_status == "verified",
                    Profile.allow_matching.is_(True),
                    Profile.embed_need.isnot(None))
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
    return f"Thema: „{need}“ — Angebot: „{offer}“."


def _llm_explanations(profile, others):
    """Один пакетный вызов на всех кандидатов. Имена и контакты в запрос не попадают."""
    lines = []
    for index, other_profile in enumerate(others, start=1):
        lines.append(f"Kandidat {index}: {offer_text(other_profile)[:600]}")
    user_text = (
        f"Gesuch der Person:\n{need_text(profile)[:800]}\n\n"
        f"Angebote der Kandidaten:\n" + "\n\n".join(lines) + "\n\n"
        "Schreibe für jeden Kandidaten einen kurzen Satz auf Deutsch, warum er zum Gesuch passt. "
        "Antworte nur mit JSON: {\"1\": \"...\", \"2\": \"...\"}."
    )
    system = ("Du erklärst Mitgliedern eines Clubs kurz, warum zwei Profile zusammenpassen. "
              "Sei konkret und sachlich, ohne Werbung. Keine Namen, keine Kontaktdaten, keine Bewertungen der Person.")
    raw = ask(system, user_text)
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
    my_version = profile_version(my_profile)
    results = []
    stale = []
    for other, value in pairs:
        cached = Match.query.filter_by(user_id=user.id, other_id=other.id).first()
        other_version = profile_version(other.profile)
        fresh = (cached is not None and cached.version_a == my_version and cached.version_b == other_version
                 and (cached.explanation_source == "llm" or not current_app.config.get("ANTHROPIC_API_KEY")))
        if not fresh:
            stale.append((other, value, cached, other_version))
        else:
            results.append((other, fit_label(value), cached.explanation))

    if stale:
        texts = {}
        try:
            texts = _llm_explanations(my_profile, [other.profile for other, _, _, _ in stale])
            source = "llm"
        except (LLMUnavailable, ValueError, KeyError, TypeError):
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
