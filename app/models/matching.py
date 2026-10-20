from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow


class Match(db.Model):
    """Кеш совпадения: оценка и объяснение для пары участников.

    Объяснение пересчитывается, если изменился текст любого из двух профилей (version_a / version_b)
    или если оно было шаблонным, а ключ LLM уже доступен (explanation_source = 'template').
    """

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    other_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text, default="")
    explanation_source: Mapped[str] = mapped_column(String(12), default="template")
    version_a: Mapped[str] = mapped_column(String(64))
    version_b: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "other_id", name="uq_matches_pair"),
        CheckConstraint("explanation_source IN ('llm', 'template')", name="ck_matches_source"),
    )


INTRO_STATUSES = ("pending", "accepted", "declined", "withdrawn")


class IntroRequest(db.Model):
    """Запрос на контакт между двумя участниками. Контакты видны только при status = accepted."""

    __tablename__ = "intro_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)

    from_user = relationship("User", foreign_keys=[from_user_id])
    to_user = relationship("User", foreign_keys=[to_user_id])

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'declined', 'withdrawn')", name="ck_intro_status"),
        CheckConstraint("from_user_id <> to_user_id", name="ck_intro_not_self"),
    )
