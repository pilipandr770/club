from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow

MODES = ("ascending", "descending")
LOT_STATUSES = ("open", "closed")


class Lot(db.Model):
    """Лот аукциона. Суммы в центах (целые числа), время в naive UTC.

    ascending  — повышение: побеждает последняя принятая ставка к окончанию.
    descending — понижение (голландский аукцион): цена падает каждые drop_interval_seconds,
                 побеждает первый, кто принял текущую цену.
    """

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(12))
    start_price_cents: Mapped[int] = mapped_column(Integer)
    floor_price_cents: Mapped[int | None] = mapped_column(Integer)  # только для понижения
    step_cents: Mapped[int] = mapped_column(Integer)  # шаг повышения или величина снижения
    drop_interval_seconds: Mapped[int | None] = mapped_column(Integer)  # только для понижения
    current_price_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="open")
    opens_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    winning_price_cents: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    bids: Mapped[list["Bid"]] = relationship(back_populates="lot", order_by="Bid.id")

    __table_args__ = (
        CheckConstraint("mode IN ('ascending', 'descending')", name="ck_lots_mode"),
        CheckConstraint("status IN ('open', 'closed')", name="ck_lots_status"),
        CheckConstraint("start_price_cents >= 0", name="ck_lots_start_nonneg"),
        CheckConstraint("step_cents > 0", name="ck_lots_step_positive"),
    )


class Bid(db.Model):
    """Ставка. Только добавляется: строки не меняются и не удаляются."""

    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"), index=True)
    lot: Mapped[Lot] = relationship(back_populates="bids")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
