from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow

TRIP_KINDS = ("shared", "offer")  # shared — совместная поездка участника, offer — предложение оператора или агента
TRIP_STATUSES = ("pending", "open", "rejected", "cancelled")  # pending/rejected — только для предложений
BOOKING_STATUSES = ("confirmed", "cancelled")


class Trip(db.Model):
    """Совместная поездка или предложение. Места ведутся через Booking, сумма мест не хранится."""

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    destination: Mapped[str] = mapped_column(String(120))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    seats_total: Mapped[int] = mapped_column(Integer)
    price_per_person_cents: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    author = relationship("User")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="trip", cascade="all, delete-orphan",
                                                     order_by="Booking.created_at")

    __table_args__ = (
        CheckConstraint("kind IN ('shared', 'offer')", name="ck_trips_kind"),
        CheckConstraint("status IN ('pending', 'open', 'rejected', 'cancelled')", name="ck_trips_status"),
        CheckConstraint("seats_total > 0", name="ck_trips_seats_positive"),
        CheckConstraint("ends_on >= starts_on", name="ck_trips_dates"),
    )


class Booking(db.Model):
    """Место в поездке. Отмена меняет статус, строка остаётся для истории."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    trip: Mapped[Trip] = relationship(back_populates="bookings")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seats: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("trip_id", "user_id", name="uq_booking_trip_user"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_bookings_status"),
        CheckConstraint("seats > 0", name="ck_bookings_seats_positive"),
    )
