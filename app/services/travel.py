"""Путешествия: совместные поездки участников и предложения операторов и агентов.

Оплата в этой версии не проводится: цена за человека — ориентир, договорённость о деньгах — отдельно.
Места считаются под блокировкой строки поездки (FOR UPDATE), чтобы не продать одно место дважды.
"""

from datetime import date

from sqlalchemy import select

from ..extensions import db
from ..models import Booking, Trip
from ..models.user import ROLES
from ..travel_guide import TRIP_FIELDS
from ..utils import eur_to_cents, utcnow
from .audit import audit
from .media import attach_files

OFFER_ROLES = ("travel_operator", "travel_agent")
TITLE_MAX = 160
DESCRIPTION_MAX = 2000
DESTINATION_MAX = 120
MAX_SEATS_PER_BOOKING = 6
MAX_SEATS_TOTAL = 500


class TravelError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_trip_form(form, today: date | None = None) -> tuple[dict, list[str]]:
    today = today or date.today()
    errors = []
    data = {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "destination": form.get("destination", "").strip(),
    }
    if not data["title"] or len(data["title"]) > TITLE_MAX:
        errors.append("title")
    if len(data["description"]) > DESCRIPTION_MAX:
        errors.append("description")
    for field in TRIP_FIELDS[1:]:  # остальные разделы карточки необязательны
        value = form.get(field.key, "").strip()
        if len(value) > field.max_length:
            errors.append(field.key)
        data[field.key] = value
    if not data["destination"] or len(data["destination"]) > DESTINATION_MAX:
        errors.append("destination")

    try:
        data["starts_on"] = date.fromisoformat(form.get("starts_on", ""))
        data["ends_on"] = date.fromisoformat(form.get("ends_on", ""))
        if data["starts_on"] < today or data["ends_on"] < data["starts_on"]:
            errors.append("dates")
    except ValueError:
        errors.append("dates")

    try:
        seats = int(form.get("seats_total", ""))
        if not 1 <= seats <= MAX_SEATS_TOTAL:
            raise ValueError
        data["seats_total"] = seats
    except ValueError:
        errors.append("seats_total")

    raw_price = form.get("price", "").strip()
    data["price_per_person_cents"] = None
    if raw_price:
        try:
            data["price_per_person_cents"] = eur_to_cents(raw_price)
        except ValueError:
            errors.append("price")
    return data, errors


def save_files(trip: Trip, storage, cover, program, remove_cover=False, remove_program=False, *,
               image_max_bytes: int, doc_max_bytes: int) -> list[str]:
    """Обложка и PDF поездки; проверки идут до записи. Бросает MediaError, возвращает старые ключи."""
    return attach_files(trip, storage, f"trips/{trip.id}", cover, program, remove_cover, remove_program,
                        attrs=("cover_key", "program_key", "program_text"), image_max_bytes=image_max_bytes,
                        doc_max_bytes=doc_max_bytes)


def edit_trip(author, trip: Trip, data) -> None:
    """Правка своей поездки. Места нельзя уменьшить ниже уже занятых; предложение оператора идёт на повторную проверку."""
    if trip.author_id != author.id:
        raise TravelError("forbidden")
    if trip.status not in ("open", "pending", "rejected"):
        raise TravelError("not_open")
    if trip.ends_on < date.today():
        raise TravelError("in_past")
    if data["seats_total"] < seats_taken(trip.id):
        raise TravelError("seats_below_booked")
    for key, value in data.items():
        setattr(trip, key, value)
    if trip.kind == "offer":
        trip.status = "pending"
        trip.rejection_reason = None
    audit("travel.trip_edited", target=f"trip:{trip.id}", actor_id=author.id)


def create_shared_trip(author, data) -> Trip:
    trip = Trip(author_id=author.id, kind="shared", status="open", **data)
    db.session.add(trip)
    db.session.flush()
    audit("travel.trip_created", target=f"trip:{trip.id}", actor_id=author.id)
    return trip


def create_offer(author, data) -> Trip:
    if author.role not in OFFER_ROLES:
        raise TravelError("not_operator")
    trip = Trip(author_id=author.id, kind="offer", status="pending", **data)
    db.session.add(trip)
    db.session.flush()
    audit("travel.offer_submitted", target=f"trip:{trip.id}", actor_id=author.id)
    return trip


def approve_offer(admin, trip: Trip) -> None:
    trip.status = "open"
    trip.rejection_reason = None
    audit("travel.offer_approved", target=f"trip:{trip.id}", actor_id=admin.id)


def reject_offer(admin, trip: Trip, reason: str) -> None:
    trip.status = "rejected"
    trip.rejection_reason = reason.strip()[:300] or None
    audit("travel.offer_rejected", target=f"trip:{trip.id}", details=trip.rejection_reason or "",
          actor_id=admin.id)


def seats_taken(trip_id: int) -> int:
    rows = db.session.query(Booking.seats).filter_by(trip_id=trip_id, status="confirmed").all()
    return sum(seats for (seats,) in rows)


def seats_left(trip: Trip) -> int:
    return max(0, trip.seats_total - seats_taken(trip.id))


def join(user, trip_id: int, seats: int, today: date | None = None, share_contact: bool = False) -> Booking:
    """Записывает участника. Вызывающий код делает commit при успехе и rollback при TravelError."""
    today = today or date.today()
    if not 1 <= seats <= MAX_SEATS_PER_BOOKING:
        raise TravelError("bad_seats")
    if not share_contact:  # организатору нужен способ связаться: без явного согласия записаться нельзя
        raise TravelError("contact_needed")
    trip = db.session.execute(select(Trip).where(Trip.id == trip_id).with_for_update()).scalar_one_or_none()
    if trip is None:
        raise TravelError("not_found")
    if trip.status != "open":
        raise TravelError("not_open")
    if trip.ends_on < today:
        raise TravelError("in_past")
    if trip.author_id == user.id:
        raise TravelError("own_trip")

    existing = Booking.query.filter_by(trip_id=trip.id, user_id=user.id).first()
    if existing is not None and existing.status == "confirmed":
        raise TravelError("already_booked")
    if seats > seats_left(trip):
        raise TravelError("not_enough_seats")

    if existing is not None:  # участник возвращается после отмены: строка та же, история сохраняется
        existing.status = "confirmed"
        existing.seats = seats
        existing.cancelled_at = None
        existing.share_contact = True
        booking = existing
    else:
        booking = Booking(trip_id=trip.id, user_id=user.id, seats=seats, status="confirmed", share_contact=True)
        db.session.add(booking)
    db.session.flush()
    audit("travel.booked", target=f"trip:{trip.id}", details=f"seats={seats}", actor_id=user.id)
    return booking


def leave(user, trip_id: int) -> None:
    booking = Booking.query.filter_by(trip_id=trip_id, user_id=user.id, status="confirmed").first()
    if booking is None:
        raise TravelError("not_booked")
    booking.status = "cancelled"
    booking.cancelled_at = utcnow()
    audit("travel.cancelled_booking", target=f"trip:{trip_id}", actor_id=user.id)


def cancel_trip(author, trip: Trip) -> None:
    if trip.author_id != author.id:
        raise TravelError("forbidden")
    if trip.status not in ("open", "pending"):
        raise TravelError("not_open")
    trip.status = "cancelled"
    audit("travel.trip_cancelled", target=f"trip:{trip.id}", actor_id=author.id)


def is_offer_author(user) -> bool:
    return user is not None and user.role in OFFER_ROLES and user.role in ROLES
