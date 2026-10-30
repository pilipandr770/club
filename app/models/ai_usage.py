"""Расход ИИ по дням. Только счётчики по функции и модели; ни людей, ни текстов запросов здесь нет.

Нужен для двух вещей: страница расходов в админке и предохранитель (суточный и месячный лимит токенов),
чтобы один всплеск запросов или бот не сжёг бюджет (services/ai/usage.py).
"""

from datetime import date

from sqlalchemy import BigInteger, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class AiUsage(db.Model):
    __tablename__ = "ai_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    feature: Mapped[str] = mapped_column(String(24), primary_key=True)
    model: Mapped[str] = mapped_column(String(80), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
