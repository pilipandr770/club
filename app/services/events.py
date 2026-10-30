"""Встречи участников: создание, модерация, запись, приглашения, отмена.

Оплаты нет: цена — ориентир, договорённость о деньгах между организатором и гостями.
Места считаются под блокировкой строки встречи (FOR UPDATE), чтобы не записать лишних.
E-mail гостя организатор видит только с явного согласия при записи; приглашённых он не видит вовсе.
"""

from datetime import datetime, timedelta

from flask import current_app
from flask_babel import force_locale
from flask_babel import lazy_gettext as _l
from sqlalchemy import select

from ..event_guide import EVENT_FIELDS
from ..extensions import db
from ..models import Event, EventInvite, EventRsvp, User
from ..models.events import EVENT_FORMATS, EVENT_TYPES
from ..utils import clean_url, eur_to_cents, format_eur, local_to_utc, utcnow
from .audit import audit
from .mailer import send_mail

TITLE_MAX = 160
MAX_DURATION = timedelta(days=14)
MAX_GUESTS = 5  # мест на одну запись, считая самого участника
MAX_INVITES = 20
MAX_CAPACITY = 1000

INVITE_SUBJECT = _l("Invitation: %(title)s")
INVITE_BODY = _l("%(organizer)s invites you to the meeting \"%(title)s\" on %(when)s.\n"
                 "Details and sign-up: %(url)s")
CANCEL_SUBJECT = _l("Meeting cancelled: %(title)s")
CANCEL_BODY = _l("The meeting \"%(title)s\" on %(when)s has been cancelled by the organiser.")


class EventError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_event_form(form, now: datetime | None = None) -> tuple[dict, list[str]]:
    """Разбирает форму встречи. Время вводится по Берлину. Возвращает (данные, коды ошибок)."""
    now = now or utcnow()
    errors = []
    data = {
        "title": form.get("title", "").strip(),
        "event_type": form.get("event_type", "") if form.get("event_type") in EVENT_TYPES else "meetup",
        "event_format": form.get("event_format", ""),
        "language": form.get("language", "").strip()[:60],
        "venue": form.get("venue", "").strip()[:160],
        "address": form.get("address", "").strip()[:300],
    }
    if not data["title"] or len(data["title"]) > TITLE_MAX:
        errors.append("title")
    if data["event_format"] not in EVENT_FORMATS:
        errors.append("event_format")
    for field in EVENT_FIELDS:
        value = form.get(field.key, "").strip()
        if (field.required and not value) or len(value) > field.max_length:
            errors.append(field.key)
        data[field.key] = value

    raw_url = form.get("online_url", "").strip()
    data["online_url"] = None
    if raw_url:
        cleaned = clean_url(raw_url)
        if cleaned is None:
            errors.append("online_url")
        else:
            data["online_url"] = cleaned[:300]
    if data["event_format"] in ("in_person", "hybrid") and not data["venue"]:
        errors.append("venue")
    if data["event_format"] in ("online", "hybrid") and not data["online_url"] and "online_url" not in errors:
        errors.append("online_url")

    try:
        data["starts_at"] = local_to_utc(form.get("starts_at", ""))
        data["ends_at"] = local_to_utc(form.get("ends_at", ""))
        if data["starts_at"] <= now or data["ends_at"] <= data["starts_at"] \
                or data["ends_at"] - data["starts_at"] > MAX_DURATION:
            errors.append("times")
    except ValueError:
        errors.append("times")

    data["capacity"] = None
    raw_capacity = form.get("capacity", "").strip()
    if raw_capacity:
        try:
            capacity = int(raw_capacity)
            if not 1 <= capacity <= MAX_CAPACITY:
                raise ValueError
            data["capacity"] = capacity
        except ValueError:
            errors.append("capacity")
    data["price_cents"] = None
    raw_price = form.get("price", "").strip()
    if raw_price:
        try:
            data["price_cents"] = eur_to_cents(raw_price)
        except ValueError:
            errors.append("price")
    return data, errors


def going_seats(event_id: int) -> int:
    rows = db.session.query(EventRsvp.guests).filter_by(event_id=event_id, status="going").all()
    return sum(guests for (guests,) in rows)


def seats_left(event: Event):
    """Свободных мест или None, если число мест не ограничено."""
    if event.capacity is None:
        return None
    return max(0, event.capacity - going_seats(event.id))


def create_event(organizer, data) -> Event:
    """Встреча участника ждёт проверки; встреча администратора открывается сразу."""
    status = "open" if organizer.is_admin else "pending"
    event = Event(organizer_id=organizer.id, status=status, **data)
    db.session.add(event)
    db.session.flush()
    audit("event.created" if status == "open" else "event.submitted", target=f"event:{event.id}",
          actor_id=organizer.id)
    return event


def edit_event(organizer, event: Event, data, now: datetime | None = None) -> None:
    """Правка своей встречи. Любая правка участника снова отправляет её на проверку."""
    now = now or utcnow()
    if event.organizer_id != organizer.id:
        raise EventError("forbidden")
    if event.status == "cancelled" or event.ends_at < now:
        raise EventError("not_editable")
    if data["capacity"] is not None and data["capacity"] < going_seats(event.id):
        raise EventError("capacity_below_going")
    for key, value in data.items():
        setattr(event, key, value)
    if not organizer.is_admin:
        event.status = "pending"
        event.rejection_reason = None
    audit("event.edited", target=f"event:{event.id}", actor_id=organizer.id)


def approve_event(admin, event: Event) -> None:
    if event.status != "pending":
        raise EventError("not_pending")
    event.status = "open"
    event.rejection_reason = None
    audit("event.approved", target=f"event:{event.id}", actor_id=admin.id)


def reject_event(admin, event: Event, reason: str) -> None:
    if event.status != "pending":
        raise EventError("not_pending")
    event.status = "rejected"
    event.rejection_reason = reason.strip()[:300] or None
    audit("event.rejected", target=f"event:{event.id}", details=event.rejection_reason or "", actor_id=admin.id)


def _when(user: User, moment: datetime) -> str:
    from flask_babel import format_datetime

    return format_datetime(moment, "dd.MM.yyyy HH:mm")


def _event_url(event: Event) -> str:
    return f"{current_app.config['BASE_URL'].rstrip('/')}/events/{event.id}"


def cancel_event(actor, event: Event) -> list[User]:
    """Отмена организатором или администратором. Возвращает записавшихся: им уйдут письма после commit."""
    if event.organizer_id != actor.id and not getattr(actor, "is_admin", False):
        raise EventError("forbidden")
    if event.status == "cancelled":
        return []
    event.status = "cancelled"
    audit("event.cancelled", target=f"event:{event.id}", actor_id=actor.id)
    return [r.user for r in event.rsvps if r.status == "going"]


def notify_cancelled(event: Event, users: list[User]) -> None:
    for user in users:
        if not user.email:
            continue
        with force_locale(user.locale or "de"):
            values = {"title": event.title, "when": _when(user, event.starts_at)}
            send_mail(user.email, str(CANCEL_SUBJECT) % values, str(CANCEL_BODY) % values)


def join(user, event_id: int, guests: int, share_contact: bool, now: datetime | None = None) -> EventRsvp:
    """Записывает участника. Вызывающий делает commit при успехе и rollback при EventError."""
    now = now or utcnow()
    if not 1 <= guests <= MAX_GUESTS:
        raise EventError("bad_guests")
    if not share_contact:
        raise EventError("contact_needed")
    event = db.session.execute(select(Event).where(Event.id == event_id).with_for_update()).scalar_one_or_none()
    if event is None:
        raise EventError("not_found")
    if event.status != "open":
        raise EventError("not_open")
    if event.ends_at < now:
        raise EventError("in_past")
    if event.organizer_id == user.id:
        raise EventError("own_event")
    existing = EventRsvp.query.filter_by(event_id=event.id, user_id=user.id).first()
    if existing is not None and existing.status == "going":
        raise EventError("already_going")
    if event.capacity is not None and guests > event.capacity - going_seats(event.id):
        raise EventError("full")
    if existing is not None:
        existing.status, existing.guests, existing.cancelled_at, existing.share_contact = "going", guests, None, True
        rsvp = existing
    else:
        rsvp = EventRsvp(event_id=event.id, user_id=user.id, guests=guests, status="going", share_contact=True)
        db.session.add(rsvp)
    db.session.flush()
    audit("event.joined", target=f"event:{event.id}", details=f"seats={guests}", actor_id=user.id)
    return rsvp


def leave(user, event_id: int) -> None:
    rsvp = EventRsvp.query.filter_by(event_id=event_id, user_id=user.id, status="going").first()
    if rsvp is None:
        raise EventError("not_going")
    rsvp.status = "cancelled"
    rsvp.cancelled_at = utcnow()
    audit("event.left", target=f"event:{event_id}", actor_id=user.id)


def invitable_members(event: Event):
    """Участники, которых можно пригласить: проверенные, в каталоге, ещё не приглашены и не записаны."""
    from ..models import Profile

    taken = {i.user_id for i in event.invites} | {r.user_id for r in event.rsvps if r.status == "going"}
    taken.add(event.organizer_id)
    query = (User.query.join(Profile, Profile.user_id == User.id)
             .filter(Profile.visible_in_directory.is_(True), User.status == "active",
                     User.verification_status == "verified", User.email_confirmed_at.isnot(None))
             .order_by(User.first_name).limit(300))
    return [user for user in query.all() if user.id not in taken]


def invite_members(inviter, event: Event, user_ids: list[int]) -> list[User]:
    """Создаёт приглашения. Возвращает приглашённых: письма уходят после commit (notify_invited)."""
    if event.organizer_id != inviter.id:
        raise EventError("forbidden")
    if event.status != "open":
        raise EventError("not_open")
    allowed = {user.id: user for user in invitable_members(event)}
    chosen = [allowed[uid] for uid in dict.fromkeys(user_ids) if uid in allowed]
    if not chosen:
        raise EventError("nobody")
    if len(event.invites) + len(chosen) > MAX_INVITES:
        raise EventError("too_many_invites")
    for user in chosen:
        db.session.add(EventInvite(event_id=event.id, user_id=user.id, invited_by_id=inviter.id))
    db.session.flush()
    audit("event.invited", target=f"event:{event.id}", details=f"count={len(chosen)}", actor_id=inviter.id)
    return chosen


def notify_invited(event: Event, users: list[User]) -> None:
    for user in users:
        if not user.email:
            continue
        with force_locale(user.locale or "de"):
            values = {"title": event.title, "organizer": event.organizer.first_name,
                      "when": _when(user, event.starts_at), "url": _event_url(event)}
            send_mail(user.email, str(INVITE_SUBJECT) % values, str(INVITE_BODY) % values)


def _ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def to_ics(event: Event) -> str:
    """Календарный файл (UTC) для «добавить в календарь». Ссылка на онлайн-встречу сюда не попадает."""
    stamp = "%Y%m%dT%H%M%SZ"
    where = ", ".join(part for part in (event.venue, event.address) if part)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Club//Events//DE", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
             f"UID:event-{event.id}@club", f"DTSTAMP:{utcnow().strftime(stamp)}",
             f"DTSTART:{event.starts_at.strftime(stamp)}", f"DTEND:{event.ends_at.strftime(stamp)}",
             f"SUMMARY:{_ics_escape(event.title)}", f"LOCATION:{_ics_escape(where)}",
             f"DESCRIPTION:{_ics_escape(event.description[:500])}", f"URL:{_event_url(event)}",
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def price_text(event: Event):
    return format_eur(event.price_cents) if event.price_cents else None
