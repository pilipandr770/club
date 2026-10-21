from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow
from .user import User  # noqa: F401  (для аннотации owner)

STAGES = ("idea", "prototype", "revenue", "growth")
CATEGORIES = ("fintech", "health", "climate", "ai", "education", "consumer", "other")
STARTUP_STATUSES = ("pending", "approved", "rejected")
INTEREST_STATUSES = ("pending", "accepted", "declined")


class Startup(db.Model):
    """Стартап участника. Виден в каталоге только после модерации (status = approved)."""

    __tablename__ = "startups"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    tagline: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Структура питча: каждое поле — отдельный абзац карточки проекта (см. startup_guide.py)
    problem: Mapped[str] = mapped_column(Text, default="", server_default="")
    solution: Mapped[str] = mapped_column(Text, default="", server_default="")
    market: Mapped[str] = mapped_column(Text, default="", server_default="")
    traction: Mapped[str] = mapped_column(Text, default="", server_default="")
    business_model: Mapped[str] = mapped_column(Text, default="", server_default="")
    team: Mapped[str] = mapped_column(Text, default="", server_default="")
    use_of_funds: Mapped[str] = mapped_column(Text, default="", server_default="")
    logo_key: Mapped[str | None] = mapped_column(String(200))  # картинка проекта, хранится за интерфейсом media
    deck_key: Mapped[str | None] = mapped_column(String(200))  # PDF-презентация (пересобранная)
    deck_text: Mapped[str] = mapped_column(Text, default="", server_default="")  # текст из PDF для помощника Clubi
    stage: Mapped[str] = mapped_column(String(12))
    category: Mapped[str] = mapped_column(String(20))
    funding_goal_cents: Mapped[int | None] = mapped_column(Integer)
    website_url: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)

    owner: Mapped["User"] = relationship()
    interests: Mapped[list["InvestorInterest"]] = relationship(
        back_populates="startup", cascade="all, delete-orphan", order_by="InvestorInterest.created_at"
    )

    __table_args__ = (
        CheckConstraint("stage IN ('idea', 'prototype', 'revenue', 'growth')", name="ck_startups_stage"),
        CheckConstraint(
            "category IN ('fintech', 'health', 'climate', 'ai', 'education', 'consumer', 'other')",
            name="ck_startups_category",
        ),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_startups_status"),
    )


class InvestorInterest(db.Model):
    """Интерес инвестора к стартапу. Контакты основателя видны инвестору только после status = accepted."""

    __tablename__ = "investor_interests"

    id: Mapped[int] = mapped_column(primary_key=True)
    startup_id: Mapped[int] = mapped_column(ForeignKey("startups.id", ondelete="CASCADE"), index=True)
    startup: Mapped[Startup] = relationship(back_populates="interests")
    investor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("startup_id", "investor_id", name="uq_interest_startup_investor"),
        CheckConstraint("status IN ('pending', 'accepted', 'declined')", name="ck_interest_status"),
    )
