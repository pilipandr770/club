from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow

VERIFICATION_STATUSES = ("none", "pending", "verified", "failed", "manual")


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)

    # Язык интерфейса и писем, часовой пояс участника (ARCHITECTURE.md §5)
    locale: Mapped[str] = mapped_column(String(10), default="de")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")

    # Проверка личности через Stripe Identity (ARCHITECTURE.md §10).
    # Документы и биометрию клуб не хранит — только статус и ссылку на сессию.
    verification_status: Mapped[str] = mapped_column(String(16), default="none")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    stripe_verification_session_id: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    profile: Mapped["Profile | None"] = relationship(back_populates="user", uselist=False)
    consents: Mapped[list["Consent"]] = relationship(
        back_populates="user", order_by="Consent.granted_at"
    )

    __table_args__ = (
        CheckConstraint(
            "verification_status IN ('none', 'pending', 'verified', 'failed', 'manual')",
            name="ck_users_verification_status",
        ),
    )


class Profile(db.Model):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120))

    # Страна, с которой участник себя связывает, и страна проживания.
    # Страна проживания обязательна при регистрации (проверяется в форме, фаза 1).
    country: Mapped[str | None] = mapped_column(String(2))
    residence_country: Mapped[str | None] = mapped_column(String(2))

    user: Mapped[User] = relationship(back_populates="profile")


class Consent(db.Model):
    """Согласия append-only: строки не меняются и не удаляются. Запись — только через record_consent (фаза 1)."""

    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    document_version: Mapped[str] = mapped_column(String(20))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="consents")
