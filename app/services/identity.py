"""Проверка личности за интерфейсом IdentityProvider (ARCHITECTURE.md §10).

Stripe — первый адаптер. Документы, селфи и даты рождения хранит Stripe; клуб получает
только результат: статус, страну документа и факт «18 лет и старше».
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import stripe

ALLOWED_DOCUMENT_TYPES = ["driving_license", "passport", "id_card"]


@dataclass(frozen=True)
class IdentityResult:
    status: str  # verified | requires_input | processing | canceled
    document_country: str | None = None  # ISO 3166-1 alpha-2, например "DE"
    is_adult: bool | None = None  # None, если возраст не удалось определить


class IdentityProvider(Protocol):
    def create_session(self, user_id: int, return_url: str) -> tuple[str, str]:
        """Возвращает (session_id, url) для перехода участника на страницу проверки."""

    def construct_event(self, payload: bytes, signature: str):
        """Проверяет подпись вебхука и возвращает событие. Бросает исключение при неверной подписи."""

    def fetch_result(self, session_id: str) -> IdentityResult:
        """Читает итог проверки у провайдера."""

    def redact(self, session_id: str) -> None:
        """Удаляет персональные данные проверки у провайдера (Art. 17 DSGVO)."""


def is_adult(dob: dict | None, today: date) -> bool | None:
    """True, если человеку исполнилось 18 лет к дате today. Дату рождения не сохраняем."""
    if not dob:
        return None
    try:
        birth = date(int(dob["year"]), int(dob["month"]), int(dob["day"]))
    except (KeyError, TypeError, ValueError):
        return None
    try:
        eighteenth = birth.replace(year=birth.year + 18)
    except ValueError:  # 29 февраля
        eighteenth = date(birth.year + 18, 3, 1)
    return today >= eighteenth


class StripeIdentityProvider:
    def __init__(self, api_key: str, webhook_secret: str):
        self.api_key = api_key
        self.webhook_secret = webhook_secret

    def _require_key(self):
        if not self.api_key:
            raise RuntimeError("STRIPE_SECRET_KEY не задан в .env")

    def create_session(self, user_id, return_url):
        self._require_key()
        session = stripe.identity.VerificationSession.create(
            api_key=self.api_key,
            type="document",
            metadata={"user_id": str(user_id)},
            return_url=return_url,
            options={
                "document": {
                    "allowed_types": ALLOWED_DOCUMENT_TYPES,
                    "require_live_capture": True,
                    "require_matching_selfie": True,
                }
            },
        )
        return session["id"], session["url"]

    def construct_event(self, payload, signature):
        if not self.webhook_secret:
            raise RuntimeError("STRIPE_WEBHOOK_SECRET не задан в .env")
        return stripe.Webhook.construct_event(payload, signature, self.webhook_secret)

    def fetch_result(self, session_id):
        self._require_key()
        session = stripe.identity.VerificationSession.retrieve(
            session_id,
            api_key=self.api_key,
            expand=["last_verification_report"],
        )
        status = session["status"]
        if status != "verified":
            return IdentityResult(status=status)
        report = session.get("last_verification_report") or {}
        document = report.get("document") or {}
        outputs = session.get("verified_outputs") or {}
        return IdentityResult(
            status=status,
            document_country=(document.get("issuing_country") or None),
            is_adult=is_adult(outputs.get("dob"), date.today()),
        )

    def redact(self, session_id):
        self._require_key()
        stripe.identity.VerificationSession.redact(session_id, api_key=self.api_key)
