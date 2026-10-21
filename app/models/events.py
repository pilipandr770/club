from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..utils import utcnow

EVENT_TYPES = ("meetup", "workshop", "talk", "dinner", "outdoor", "online", "other")
EVENT_FORMATS = ("in_person", "online", "hybrid")
EVENT_STATUSES = ("pending", "open", "rejected", "cancelled")
RSVP_STATUSES = ("going", "cancelled")


class Event(db.Model):
    """Встреча, созданная участником. Публикуется после проверки администратором (status = open).

    Время хранится в naive UTC, как везде; в формах и на страницах показывается во времени пользователя.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    organizer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(12), default="meetup")
    event_format: Mapped[str] = mapped_column(String(10), default="in_person")
    # О чём, для кого, как проходит (см. event_guide.py)
    description: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(Text, default="", server_default="")
    agenda: Mapped[str] = mapped_column(Text, default="", server_default="")
    requirements: Mapped[str] = mapped_column(Text, default="", server_default="")
    language: Mapped[str] = mapped_column(String(60), default="", server_default="")
    # Где: название места и общий адрес (не частный); ссылка на онлайн-встречу видна только записавшимся
    venue: Mapped[str] = mapped_column(String(160), default="", server_default="")
    address: Mapped[str] = mapped_column(String(300), default="", server_default="")
    online_url: Mapped[str | None] = mapped_column(String(300))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    capacity: Mapped[int | None] = mapped_column(Integer)  # None — без ограничения
    price_cents: Mapped[int | None] = mapped_column(Integer)  # ориентир; клуб деньги не ведёт
    image_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    organizer = relationship("User")
    rsvps: Mapped[list["EventRsvp"]] = relationship(back_populates="event", cascade="all, delete-orphan",
                                                    order_by="EventRsvp.created_at")
    invites: Mapped[list["EventInvite"]] = relationship(back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'open', 'rejected', 'cancelled')", name="ck_events_status"),
        CheckConstraint("ends_at > starts_at", name="ck_events_times"),
    )


class EventRsvp(db.Model):
    """Запись участника на встречу. Отмена меняет статус, строка остаётся."""

    __tablename__ = "event_rsvps"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    event: Mapped[Event] = relationship(back_populates="rsvps")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user = relationship("User")
    guests: Mapped[int] = mapped_column(Integer, default=1)  # мест с учётом гостей
    status: Mapped[str] = mapped_column(String(12), default="going")
    # Участник разрешил организатору увидеть свой e-mail (флажок при записи)
    share_contact: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_rsvps_event_user"),)


class EventInvite(db.Model):
    """Личное приглашение участнику. Адрес приглашённого организатору не показывается."""

    __tablename__ = "event_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    event: Mapped[Event] = relationship(back_populates="invites")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user = relationship("User", foreign_keys=[user_id])
    invited_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_invites_event_user"),)
