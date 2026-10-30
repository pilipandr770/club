"""Учёт расхода ИИ и предохранитель.

record() добавляет вызов в счётчики за сегодня (функция + модель). Запись идёт в отдельной короткой транзакции
и глотает ошибки: учёт никогда не ломает ответ. check_budget() вызывается перед запросом к модели и бросает
AIUnavailable("budget"), если исчерпан суточный или месячный лимит токенов; вызывающий код при этом уходит
на запасной шаблон, как и при недоступном провайдере.
В лимит входят только языковые модели; векторы и распознавание речи дёшевы, и без них не работает подбор.
"""

import logging
from datetime import timedelta

from flask import current_app
from sqlalchemy import text

from ...extensions import db
from ...utils import utcnow
from .http import AIUnavailable

log = logging.getLogger(__name__)

# Функции, расход которых считается токенами языковой модели
NOT_LLM = ("embeddings", "voice")
# Ошибки настройки: провайдер вообще не вызывали, в сбои их не пишем
NOT_CALLED = ("disabled", "no_key", "no_base_url", "no_model", "unknown_provider", "budget", "bad_audio")


def record(feature: str, model: str, tokens_in: int = 0, tokens_out: int = 0, failed: bool = False) -> None:
    try:
        with db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO ai_usage (day, feature, model, calls, failures, tokens_in, tokens_out) "
                "VALUES (:day, :feature, :model, :calls, :failures, :tin, :tout) "
                "ON CONFLICT (day, feature, model) DO UPDATE SET calls = ai_usage.calls + :calls, "
                "failures = ai_usage.failures + :failures, tokens_in = ai_usage.tokens_in + :tin, "
                "tokens_out = ai_usage.tokens_out + :tout"),
                {"day": utcnow().date(), "feature": feature[:24], "model": (model or "")[:80],
                 "calls": 0 if failed else 1, "failures": 1 if failed else 0,
                 "tin": max(0, int(tokens_in or 0)), "tout": max(0, int(tokens_out or 0))})
    except Exception:  # noqa: BLE001  учёт не должен ломать ответ
        log.warning("AI-Nutzung nicht gezählt", exc_info=True)


def record_failure(feature: str, model: str, exc: AIUnavailable) -> None:
    if str(exc) not in NOT_CALLED:
        record(feature, model, failed=True)


def _llm_tokens(conn, since, feature=None) -> int:
    query = ("SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM ai_usage "
             "WHERE day >= :since AND feature NOT IN ('embeddings', 'voice')")
    params = {"since": since}
    if feature:
        query += " AND feature = :feature AND day = :since"
        params["feature"] = feature
    return int(conn.execute(text(query), params).scalar() or 0)


def _limit(name: str) -> int:
    return int(current_app.config.get(name) or 0)


def check_budget(feature: str) -> None:
    """Бросает AIUnavailable("budget"), если лимит токенов исчерпан. Лимит 0 выключает проверку."""
    daily, monthly, public = _limit("AI_DAILY_TOKEN_BUDGET"), _limit("AI_MONTHLY_TOKEN_BUDGET"), _limit("AI_PUBLIC_DAILY_TOKENS")
    if feature in NOT_LLM or not (daily or monthly or public):
        return
    today = utcnow().date()
    try:
        with db.engine.connect() as conn:
            if daily and _llm_tokens(conn, today) >= daily:
                over = "daily"
            elif monthly and _llm_tokens(conn, today.replace(day=1)) >= monthly:
                over = "monthly"
            elif public and feature == "public_chat" and _llm_tokens(conn, today, "public_chat") >= public:
                over = "public"
            else:
                return
    except Exception:  # noqa: BLE001  при сбое БД не блокируем ответы
        log.warning("AI-Budget nicht prüfbar", exc_info=True)
        return
    log.warning("AI-Budget erschöpft (%s), Funktion %s", over, feature)
    raise AIUnavailable("budget")


# --------------------------------------------------------------------------- отчёт для админки
def _cost(tokens_in: int, tokens_out: int) -> float:
    cfg = current_app.config
    return (tokens_in * float(cfg.get("AI_PRICE_IN_PER_M_USD") or 0)
            + tokens_out * float(cfg.get("AI_PRICE_OUT_PER_M_USD") or 0)) / 1_000_000


def summary() -> dict:
    """Сегодня и текущий месяц: токены, лимиты, оценка стоимости языковой модели в долларах."""
    today = utcnow().date()
    result = {}
    with db.engine.connect() as conn:
        for key, since in (("today", today), ("month", today.replace(day=1))):
            tin, tout = conn.execute(text(
                "SELECT COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) FROM ai_usage "
                "WHERE day >= :since AND feature NOT IN ('embeddings', 'voice')"), {"since": since}).one()
            result[key] = {"tokens": int(tin) + int(tout), "cost": _cost(int(tin), int(tout))}
    for key, name in (("today", "AI_DAILY_TOKEN_BUDGET"), ("month", "AI_MONTHLY_TOKEN_BUDGET")):
        limit = _limit(name)
        used = result[key]["tokens"]
        result[key].update(limit=limit, share=min(100, round(100 * used / limit)) if limit else 0, over=bool(limit and used >= limit))
    result["blocked"] = result["today"]["over"] or result["month"]["over"]
    return result


def by_feature(days: int) -> list[dict]:
    since = utcnow().date() - timedelta(days=days - 1)
    with db.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT feature, SUM(calls), SUM(failures), SUM(tokens_in), SUM(tokens_out) FROM ai_usage "
            "WHERE day >= :since GROUP BY feature ORDER BY SUM(tokens_in + tokens_out) DESC, feature"), {"since": since}).all()
    return [{"feature": feature, "calls": int(calls), "failures": int(failures), "tokens_in": int(tin), "tokens_out": int(tout),
             "cost": None if feature in NOT_LLM else _cost(int(tin), int(tout))}
            for feature, calls, failures, tin, tout in rows]


def daily(days: int) -> list[tuple]:
    """[(день, токены языковых моделей)] за последние days дней, включая пустые."""
    today = utcnow().date()
    since = today - timedelta(days=days - 1)
    with db.engine.connect() as conn:
        rows = dict(conn.execute(text(
            "SELECT day, SUM(tokens_in + tokens_out) FROM ai_usage WHERE day >= :since "
            "AND feature NOT IN ('embeddings', 'voice') GROUP BY day"), {"since": since}).all())
    return [(since + timedelta(days=i), int(rows.get(since + timedelta(days=i), 0))) for i in range(days)]
