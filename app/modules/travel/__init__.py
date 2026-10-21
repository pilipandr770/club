from datetime import date

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Booking, Trip
from ...services.entitlements import has_feature, requires_feature
from ...services.media import MediaError
from ...services.travel import (
    TravelError,
    cancel_trip,
    create_offer,
    create_shared_trip,
    edit_trip,
    join,
    leave,
    parse_trip_form,
    save_files,
    seats_left,
)
from ...travel_guide import COVER_HINT, PROGRAM_HINT, TRIP_FIELDS
from ...utils import format_eur

bp = Blueprint("travel", __name__, url_prefix="/travel")


def _error_message(code):
    messages = {
        "not_found": _("This trip does not exist."),
        "not_open": _("This trip is not open for bookings."),
        "in_past": _("This trip has already ended."),
        "own_trip": _("You cannot book your own trip."),
        "already_booked": _("You are already booked on this trip."),
        "not_enough_seats": _("There are not enough seats left."),
        "bad_seats": _("Please choose between 1 and 6 seats."),
        "not_booked": _("You are not booked on this trip."),
        "forbidden": _("This is not available to you."),
        "not_operator": _("Only travel operators and agents can publish offers."),
        "contact_needed": _("Please allow the organiser to see your e-mail address, otherwise they cannot reach you."),
        "seats_below_booked": _("There are already more seats booked than you entered."),
    }
    return messages.get(code, _("Something went wrong. Please try again."))


def _file_error(code):
    messages = {
        "empty": _("The file is empty."),
        "too_large": _("The file is too large."),
        "too_large_pixels": _("The picture has too many pixels."),
        "not_an_image": _("This file is not a picture."),
        "bad_format": _("Please use a JPG, PNG or WebP picture."),
        "not_a_pdf": _("This file is not a readable PDF."),
        "encrypted": _("Password-protected PDFs are not accepted."),
        "too_many_pages": _("The PDF has too many pages (up to 60)."),
    }
    return messages.get(code, _("The file could not be saved."))


def _trip_view(trip, today):
    return {
        "trip": trip,
        "is_offer": trip.kind == "offer",
        "seats_left": seats_left(trip),
        "price": format_eur(trip.price_per_person_cents) if trip.price_per_person_cents else None,
        "is_past": trip.ends_on < today,
    }


def _visible_to(user, trip):
    """Открытая поездка видна всем верифицированным; черновик, отклонённая и отменённая — автору и администратору."""
    return trip.status == "open" or trip.author_id == user.id or user.is_admin


def _form_context(trip, offer, errors):
    return dict(trip=trip, offer=offer, errors=errors, form=request.form, trip_fields=TRIP_FIELDS,
                cover_hint=COVER_HINT, program_hint=PROGRAM_HINT)


def _apply_files(trip):
    cfg = current_app.config
    return save_files(
        trip, current_app.extensions["media"], request.files.get("cover"), request.files.get("program"),
        remove_cover=bool(request.form.get("remove_cover")), remove_program=bool(request.form.get("remove_program")),
        image_max_bytes=cfg["MEDIA_MAX_BYTES"], doc_max_bytes=cfg["DECK_MAX_BYTES"])


def _delete_old(keys):
    storage = current_app.extensions["media"]
    for key in keys:
        storage.delete(key)


@bp.route("/")
@requires_feature("community.read")
def index():
    today = date.today()
    kind = request.args.get("kind", "")
    query = Trip.query.filter(Trip.status == "open", Trip.ends_on >= today)
    if kind in ("shared", "offer"):
        query = query.filter(Trip.kind == kind)
    trips = query.order_by(Trip.starts_on).limit(200).all()
    return render_template("modules/travel_list.html", items=[_trip_view(t, today) for t in trips], kind=kind,
                           can_plan=has_feature(current_user, "travel.book"))


@bp.route("/<int:trip_id>")
@requires_feature("community.read")
def detail(trip_id):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if not _visible_to(current_user, trip):
        abort(404)
    today = date.today()
    view = _trip_view(trip, today)
    is_author = trip.author_id == current_user.id
    my_booking = None
    if not is_author:
        my_booking = Booking.query.filter_by(trip_id=trip.id, user_id=current_user.id, status="confirmed").first()
    can_book = has_feature(current_user, "travel.book") and trip.status == "open" and not view["is_past"] \
        and not is_author and my_booking is None
    confirmed = [b for b in trip.bookings if b.status == "confirmed"]
    participants = [b.user.first_name for b in confirmed]
    # Адреса участников видит только организатор и только тех, кто разрешил при записи
    roster = [(b.user.first_name, b.seats, b.user.email if b.share_contact else None) for b in confirmed] \
        if is_author else []
    return render_template("modules/travel_detail.html", item=view, my_booking=my_booking, can_book=can_book,
                           participants=participants, roster=roster, is_author=is_author,
                           sections=[(f.key, f.label) for f in TRIP_FIELDS[1:]],
                           can_edit=is_author and trip.status in ("open", "pending", "rejected") and not view["is_past"])


@bp.route("/<int:trip_id>/cover")
@requires_feature("community.read")
def cover(trip_id):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if not trip.cover_key or not _visible_to(current_user, trip):
        abort(404)
    data = current_app.extensions["media"].read(trip.cover_key)
    if data is None:
        abort(404)
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(
        trip.cover_key.rsplit(".", 1)[-1], "application/octet-stream")
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.route("/<int:trip_id>/program")
@requires_feature("community.read")
def program(trip_id):
    """PDF — только скачиванием, только для видимых поездок."""
    trip = db.session.get(Trip, trip_id) or abort(404)
    if not trip.program_key or not _visible_to(current_user, trip):
        abort(404)
    data = current_app.extensions["media"].read(trip.program_key)
    if data is None:
        abort(404)
    response = Response(data, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f'attachment; filename="trip-{trip.id}.pdf"'
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/<int:trip_id>/join", methods=["POST"])
@requires_feature("travel.book")
def join_trip(trip_id):
    try:
        seats = int(request.form.get("seats", "1"))
    except ValueError:
        seats = 0
    try:
        join(current_user, trip_id, seats, share_contact=bool(request.form.get("share_contact")))
        db.session.commit()
    except TravelError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("travel.detail", trip_id=trip_id))
    flash(_("You are booked. The organiser will contact you."), "success")
    return redirect(url_for("travel.detail", trip_id=trip_id))


@bp.route("/<int:trip_id>/leave", methods=["POST"])
@requires_feature("travel.book")
def leave_trip(trip_id):
    try:
        leave(current_user, trip_id)
        db.session.commit()
    except TravelError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("travel.detail", trip_id=trip_id))
    flash(_("Your booking is cancelled."), "success")
    return redirect(url_for("travel.detail", trip_id=trip_id))


def _create(offer):
    """Общая часть создания: проверка формы, запись, файлы. Возвращает (ответ или None, ошибки)."""
    data, errors = parse_trip_form(request.form)
    if errors:
        flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
        return render_template("modules/travel_form.html", **_form_context(None, offer, errors))
    try:
        trip = create_offer(current_user, data) if offer else create_shared_trip(current_user, data)
        obsolete = _apply_files(trip)
    except TravelError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("travel.index"))
    except MediaError as exc:
        db.session.rollback()
        flash(_file_error(exc.code), "error")
        return render_template("modules/travel_form.html", **_form_context(None, offer, errors))
    db.session.commit()
    _delete_old(obsolete)
    flash(_("Your offer is sent for review. We will publish it after the check.") if offer
          else _("Your trip is open for others to join."), "success")
    return redirect(url_for("travel.detail", trip_id=trip.id))


@bp.route("/new", methods=["GET", "POST"])
@requires_feature("travel.book")
def new_shared():
    if request.method == "POST":
        return _create(False)
    return render_template("modules/travel_form.html", **_form_context(None, False, []))


@bp.route("/offers/new", methods=["GET", "POST"])
@requires_feature("travel.offer")
def new_offer():
    if request.method == "POST":
        return _create(True)
    return render_template("modules/travel_form.html", **_form_context(None, True, []))


@bp.route("/<int:trip_id>/edit", methods=["GET", "POST"])
@requires_feature("community.read")
def edit(trip_id):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if trip.author_id != current_user.id:
        abort(404)
    offer = trip.kind == "offer"
    if request.method == "POST":
        data, errors = parse_trip_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/travel_form.html", **_form_context(trip, offer, errors))
        try:
            edit_trip(current_user, trip, data)
            obsolete = _apply_files(trip)
        except TravelError as exc:
            db.session.rollback()
            flash(_error_message(exc.code), "error")
            return render_template("modules/travel_form.html", **_form_context(trip, offer, []))
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/travel_form.html", **_form_context(trip, offer, []))
        db.session.commit()
        _delete_old(obsolete)
        flash(_("Your changes are sent for review.") if offer else _("Your changes are saved."), "success")
        return redirect(url_for("travel.detail", trip_id=trip.id))
    return render_template("modules/travel_form.html", **_form_context(trip, offer, []))


@bp.route("/mine")
@requires_feature("community.read")
def mine():
    today = date.today()
    authored = Trip.query.filter_by(author_id=current_user.id).order_by(Trip.starts_on.desc()).all()
    bookings = (Booking.query.filter_by(user_id=current_user.id, status="confirmed")
                .join(Trip).order_by(Trip.starts_on).all())
    return render_template("modules/travel_mine.html",
                           authored=[_trip_view(t, today) for t in authored],
                           bookings=bookings, can_book=has_feature(current_user, "travel.book"),
                           can_offer=has_feature(current_user, "travel.offer"))


@bp.route("/<int:trip_id>/cancel", methods=["POST"])
@requires_feature("community.read")
def cancel(trip_id):
    trip = db.session.get(Trip, trip_id) or abort(404)
    try:
        cancel_trip(current_user, trip)
        db.session.commit()
    except TravelError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("travel.mine"))
    flash(_("The trip is cancelled."), "success")
    return redirect(url_for("travel.mine"))
