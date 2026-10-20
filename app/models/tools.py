from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from ..utils import utcnow


class ToolRun(db.Model):
    """Запуск встроенного инструмента участником: вход, результат и отчёт. Служит и журналом для месячных лимитов."""

    __tablename__ = "tool_runs"
    __table_args__ = (Index("ix_tool_runs_user_tool_created", "user_id", "tool", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(500), default="")  # адрес сайта, вопрос или название продукта
    status: Mapped[str] = mapped_column(String(12), default="done", server_default="done")  # running | done | failed
    n_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # прогресс долгих запусков (панель)
    n_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str] = mapped_column(String(300), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
