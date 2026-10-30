"""Приглашения: админ, лично знающий человека, создаёт ссылку и тем самым ручается за него.

Кто зарегистрировался по приглашению, получает ручное подтверждение личности без Stripe (с пометкой из приглашения)
и, если задано, оплаченный тариф на несколько месяцев. Код — секрет: в журнал аудита он не попадает.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from ..utils import utcnow


class Invite(db.Model):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Кто этот человек и откуда его знает поручитель; становится пометкой о подтверждении личности
    note: Mapped[str] = mapped_column(String(300))
    # Необязательная привязка к адресу почты
    email: Mapped[str | None] = mapped_column(String(255))
    plan: Mapped[str] = mapped_column(String(20), default="guest", server_default="guest")
    months: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_uses: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
