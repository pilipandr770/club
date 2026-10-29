"""Анонимная статистика посещений. Только счётчики; персональных данных здесь нет.

visit_stats — агрегаты по дням, хранятся постоянно.
visit_seen — одноразовые отпечатки «этот посетитель уже учтён сегодня». Отпечаток считается с суточным ключом,
не обратим и не связывается между днями; строки прошлых дней удаляются (services/analytics.py: purge_old).
"""

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class VisitStat(db.Model):
    __tablename__ = "visit_stats"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(16), primary_key=True)
    page: Mapped[str] = mapped_column(String(32), primary_key=True, default="")
    country: Mapped[str] = mapped_column(String(2), primary_key=True, default="")
    city: Mapped[str] = mapped_column(String(80), primary_key=True, default="")
    source: Mapped[str] = mapped_column(String(60), primary_key=True, default="")
    device: Mapped[str] = mapped_column(String(8), primary_key=True, default="")
    count: Mapped[int] = mapped_column(Integer, default=0)


class VisitSeen(db.Model):
    __tablename__ = "visit_seen"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    vhash: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
