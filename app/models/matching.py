from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

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
