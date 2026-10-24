from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..utils import utcnow

VERIFICATION_STATUSES = ("none", "pending", "verified", "failed", "manual")
ROLES = ("member", "travel_operator", "travel_agent")

# Append-only протокол согласий (Art. 7 Abs. 1 DSGVO: Nachweisbarkeit)
CONSENT_KINDS = {
    "privacy": "Datenschutzhinweise gelesen, Verarbeitung zur Kontoführung",
    "terms": "Nutzungsbedingungen akzeptiert",
    "identity": "Identitätsprüfung über Stripe Identity (Verarbeitung durch Stripe)",
    "matching": "Profil für KI-gestütztes Matching verwenden",
    "directory": "Profil im Mitgliederverzeichnis sichtbar",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80), default="")

    # Язык интерфейса и писем, часовой пояс участника (ARCHITECTURE.md §5)
    locale: Mapped[str] = mapped_column(String(10), default="de")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")

    status: Mapped[str] = mapped_column(String(20), default="active")  # active | blocked
    plan: Mapped[str] = mapped_column(String(20), default="guest")
    # Роль в путешествиях и других модулях: member | travel_operator | travel_agent
    role: Mapped[str] = mapped_column(String(20), default="member", server_default="member")
    # Администратор клуба: доступ к админ-панели со всеми правами
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    email_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Проверка личности через Stripe Identity (ARCHITECTURE.md §10).
    # Документы, селфи, номера и даты рождения клуб не хранит.
    verification_status: Mapped[str] = mapped_column(String(16), default="none")
    verification_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Короткий код причины без персональных данных, например «sanctioned_country»
    verification_reason: Mapped[str | None] = mapped_column(String(40))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    stripe_verification_session_id: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    consents: Mapped[list["Consent"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="Consent.created_at"
    )

    __table_args__ = (
        CheckConstraint(
            "verification_status IN ('none', 'pending', 'verified', 'failed', 'manual')",
            name="ck_users_verification_status",
        ),
    )

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def is_active(self) -> bool:  # Flask-Login
        return self.status == "active"

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "verified"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Profile(db.Model):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    user: Mapped[User] = relationship(back_populates="profile")

    # Страна, с которой участник себя связывает, и страна проживания.
    # Страна проживания обязательна при регистрации (ARCHITECTURE.md §5).
    country: Mapped[str | None] = mapped_column(String(2))
    residence_country: Mapped[str | None] = mapped_column(String(2))

    # Раздел «О себе» (модуль profile). Тексты — свободные, подсказки и примеры — в app/profile_guide.py
    headline: Mapped[str] = mapped_column(String(160), default="", server_default="")
    bio: Mapped[str] = mapped_column(Text, default="", server_default="")
    company: Mapped[str] = mapped_column(String(160), default="", server_default="")
    industry: Mapped[str] = mapped_column(String(120), default="", server_default="")
    city: Mapped[str] = mapped_column(String(120), default="", server_default="")
    q_focus: Mapped[str] = mapped_column(Text, default="", server_default="")  # чем занимаюсь
    q_challenge: Mapped[str] = mapped_column(Text, default="", server_default="")  # главная задача сейчас
    q_can_help: Mapped[str] = mapped_column(Text, default="", server_default="")  # чем могу помочь
    q_looking_for: Mapped[str] = mapped_column(Text, default="", server_default="")  # кого или что ищу
    expertise: Mapped[str] = mapped_column(String(500), default="", server_default="")  # теги через запятую
    photo_key: Mapped[str | None] = mapped_column(String(160))  # ключ в хранилище, сервер задаёт сам
    # Карточка-минисайт: тема обложки, чего ищет, языки, ссылки (ссылки видны только принятым контактам)
    cover_theme: Mapped[str] = mapped_column(String(12), default="violet", server_default="violet")
    open_to: Mapped[str] = mapped_column(String(160), default="", server_default="")  # ключи через запятую
    languages: Mapped[str] = mapped_column(String(160), default="", server_default="")
    website_url: Mapped[str | None] = mapped_column(String(300))
    linkedin_url: Mapped[str | None] = mapped_column(String(300))
    # Согласия (append-only в consents). По умолчанию выключены
    visible_in_directory: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    allow_matching: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Эмбеддинги для матчинга: два вектора (нужда и предложение), пересчитываются при смене текста
    embed_need: Mapped[list | None] = mapped_column(JSON)
    embed_offer: Mapped[list | None] = mapped_column(JSON)
    embed_hash: Mapped[str | None] = mapped_column(String(64))
    embed_provider: Mapped[str | None] = mapped_column(String(40))
    embed_updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class Consent(db.Model):
    """Согласия append-only: строки не меняются и не удаляются. Запись только через services/consents.py."""

    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    user: Mapped[User] = relationship(back_populates="consents")
    kind: Mapped[str] = mapped_column(String(40))
    granted: Mapped[bool]
    version: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(40), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
