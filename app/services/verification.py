"""Состояния проверки личности. Меняет статус только подписанный вебхук или сам участник через старт сессии.

Итог проверки для участника:
- verified — допущен в клуб (гостевой тариф);
- failed + причина — отказ без повторных попыток (страна под санкциями, возраст до 18);
- manual — попытки исчерпаны или данные неоднозначны, решает поддержка.
"""

from flask import current_app
from flask_babel import gettext as _

from ..extensions import db
from ..models import WebhookEvent
from ..models.user import User
from ..utils import utcnow
from .audit import audit

EVENT_PREFIX = "identity.verification_session."
FINAL_FAILURES = ("sanctioned_country", "underage")
STARTABLE_STATUSES = ("none", "pending")


class VerificationNotAllowed(Exception):
    """Старт проверки сейчас невозможен. Атрибут reason — код для показа участнику."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def provider():
    return current_app.extensions["identity_provider"]


def start_verification(user: User) -> str:
    """Создаёт сессию у провайдера и возвращает URL для участника."""
    if user.is_verified:
        raise VerificationNotAllowed("already_verified")
    if user.email_confirmed_at is None:
        raise VerificationNotAllowed("email_not_confirmed")
    if user.status != "active":
        raise VerificationNotAllowed("account_blocked")
    if user.verification_status not in STARTABLE_STATUSES:
        raise VerificationNotAllowed("not_startable")
    if user.verification_attempts >= current_app.config["IDENTITY_MAX_ATTEMPTS"]:
        raise VerificationNotAllowed("attempts_exhausted")

    return_url = f"{current_app.config['BASE_URL']}/app/verify/return"
    session_id, url = provider().create_session(user.id, return_url)
    user.stripe_verification_session_id = session_id
    user.verification_status = "pending"
    user.verification_reason = None
    audit("verification.started", target=f"user:{user.id}", actor_id=user.id)
    return url


def _fail(user: User, reason: str) -> None:
    user.verification_reason = reason
    if reason in FINAL_FAILURES:
        user.verification_status = "failed"
        return
    user.verification_attempts += 1
    limit = current_app.config["IDENTITY_MAX_ATTEMPTS"]
    user.verification_status = "manual" if user.verification_attempts >= limit else "failed"


def _apply_verified(user: User, result) -> None:
    if result.is_adult is False:
        _fail(user, "underage")
    elif result.document_country in current_app.config["BLOCKED_DOCUMENT_COUNTRIES"]:
        _fail(user, "sanctioned_country")
    elif result.document_country is None or result.is_adult is None:
        # Не удалось определить страну или возраст: решает человек
        user.verification_status = "manual"
        user.verification_reason = "needs_review"
    else:
        user.verification_status = "verified"
        user.verification_reason = None
        user.verified_at = utcnow()


def _apply_other(user: User, status: str) -> None:
    if status == "requires_input":
        user.verification_status = "pending"
        user.verification_reason = "requires_input"
    elif status == "processing":
        user.verification_status = "pending"
        user.verification_reason = None
    elif status == "canceled":
        user.verification_attempts += 1
        limit = current_app.config["IDENTITY_MAX_ATTEMPTS"]
        if user.verification_attempts >= limit:
            user.verification_status = "manual"
        else:
            user.verification_status = "none"
        user.verification_reason = "canceled"


def handle_event(event) -> str:
    """Обрабатывает событие Stripe. Возвращает краткий итог для журнала.

    Идемпотентно: повторная доставка того же event_id ничего не меняет.
    Ошибка провайдера пробрасывается, чтобы Stripe повторил доставку: WebhookEvent
    записывается только после успешной обработки.
    """
    event_id = event["id"]
    event_type = event["type"]
    if db.session.get(WebhookEvent, event_id) is not None:
        return "duplicate"

    outcome = "ignored"
    if event_type.startswith(EVENT_PREFIX):
        status = event_type[len(EVENT_PREFIX):]
        obj = event["data"]["object"]
        session_id = obj["id"]
        user_id = (obj.get("metadata") or {}).get("user_id")
        user = db.session.get(User, int(user_id)) if user_id else None
        if user is not None:
            outcome = _apply_event_to_user(user, session_id, status)

    db.session.add(WebhookEvent(id=event_id, type=event_type))
    return outcome


def _apply_event_to_user(user, session_id, status) -> str:
    if status == "verified":
        if user.is_verified:
            return "already_verified"
        result = provider().fetch_result(session_id)
        _apply_verified(user, result)
        audit("verification.result", target=f"user:{user.id}",
              details=f"status={user.verification_status} reason={user.verification_reason}")
        return user.verification_status

    # Остальные события относятся только к текущей сессии участника
    if session_id != user.stripe_verification_session_id:
        return "stale_session"
    _apply_other(user, status)
    audit("verification.result", target=f"user:{user.id}",
          details=f"status={user.verification_status} reason={user.verification_reason}")
    return user.verification_status


def reason_message(reason: str | None) -> str | None:
    """Текст для участника по коду причины. Формулировки без упоминания гражданств."""
    messages = {
        "sanctioned_country": _("We do not accept identity documents from this country."),
        "underage": _("Membership requires that you are at least 18 years old."),
        "needs_review": _("We need to check your verification by hand. We will contact you."),
        "requires_input": _("Please try again and make sure your document is fully visible."),
        "canceled": _("The verification was cancelled. You can start it again."),
        "attempts_exhausted": _("Too many attempts. Please contact support."),
    }
    return messages.get(reason)
