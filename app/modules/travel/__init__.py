from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Booking, Trip
from ...services.entitlements import has_feature, requires_feature
from ...services.travel import (
    TravelError,
    cancel_trip,
    create_offer,
    create_shared_trip,
    join,
    leave,
    parse_trip_form,
    seats_left,
)
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
    }
    return messages.get(code, _("Something went wrong. Please try again."))


def _trip_view(trip, today):
    return {
        "trip": trip,
        "is_offer": trip.kind == "offer",
        "seats_left": seats_left(trip),
        "price": format_eur(trip.price_per_person_cents) if trip.price_per_person_cents else None,
        "is_past": trip.ends_on < today,
    }


@bp.route("/")
@requires_feature("community.read")
def index():
    today = date.today()
    kind = request.args.get("kind", "")
    query = Trip.query.filter(Trip.status == "open", Trip.ends_on >= today)
    if kind in ("shared", "offer"):
        query = query.filter(Trip.kind == kind)
    trips = query.order_by(Trip.starts_on).limit(200).all()
    return render_template("modules/travel_list.html", items=[_trip_view(t, today) for t in trips], kind=kind)


@bp.route("/<int:trip_id>")
@requires_feature("community.read")
def detail(trip_id):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if trip.status != "open" and trip.author_id != current_user.id:
        abort(404)
    today = date.today()
    view = _trip_view(trip, today)
    my_booking = None
    if current_user.is_authenticated and trip.author_id != current_user.id:
        my_booking = Booking.query.filter_by(trip_id=trip.id, user_id=current_user.id,
                                             status="confirmed").first()
    can_book = has_feature(current_user, "travel.book") and trip.status == "open" and not view["is_past"] \
        and trip.author_id != current_user.id and my_booking is None
    participants = [b.user.first_name for b in trip.bookings if b.status == "confirmed"]
    return render_template("modules/travel_detail.html", item=view, my_booking=my_booking,
                           can_book=can_book, participants=participants,
                           is_author=trip.author_id == current_user.id)


@bp.route("/<int:trip_id>/join", methods=["POST"])
@requires_feature("travel.book")
def join_trip(trip_id):
    try:
        seats = int(request.form.get("seats", "1"))
    except ValueError:
        seats = 0
    try:
        join(current_user, trip_id, seats)
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


def _form_view(offer, errors, data):
    return render_template("modules/travel_form.html", offer=offer, errors=errors, form=data)


@bp.route("/new", methods=["GET", "POST"])
@requires_feature("travel.book")
def new_shared():
    errors = []
    if request.method == "POST":
        data, errors = parse_trip_form(request.form)
        if not errors:
            create_shared_trip(current_user, data)
            db.session.commit()
            flash(_("Your trip is open for others to join."), "success")
            return redirect(url_for("travel.mine"))
        flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
    return _form_view(False, errors, request.form)


@bp.route("/offers/new", methods=["GET", "POST"])
@requires_feature("travel.offer")
def new_offer():
    errors = []
    if request.method == "POST":
        data, errors = parse_trip_form(request.form)
        if not errors:
            try:
                create_offer(current_user, data)
                db.session.commit()
                flash(_("Your offer is sent for review. We will publish it after the check."), "success")
                return redirect(url_for("travel.mine"))
            except TravelError as exc:
                db.session.rollback()
                flash(_error_message(exc.code), "error")
                return redirect(url_for("travel.index"))
        flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
    return _form_view(True, errors, request.form)


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
