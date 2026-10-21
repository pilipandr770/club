from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...event_guide import EVENT_FIELDS, IMAGE_HINT, ONLINE_HINT, VENUE_HINT
from ...extensions import db
from ...models import Event, EventInvite, EventRsvp
from ...models.events import EVENT_FORMATS, EVENT_TYPES
from ...services.entitlements import has_feature, requires_feature
from ...services.events import (
    MAX_GUESTS,
    EventError,
    cancel_event,
    create_event,
    edit_event,
    going_seats,
    invitable_members,
    invite_members,
    join,
    leave,
    notify_cancelled,
    notify_invited,
    parse_event_form,
    price_text,
    seats_left,
    to_ics,
)
from ...services.media import MediaError, attach_files
from ...utils import utcnow

bp = Blueprint("events", __name__, url_prefix="/events")


def _error_message(code):
    messages = {
        "not_found": _("This meeting does not exist."),
        "not_open": _("This meeting is not open for sign-ups."),
        "in_past": _("This meeting is already over."),
        "own_event": _("You are the organiser of this meeting."),
        "already_going": _("You are already signed up."),
        "full": _("There are not enough places left."),
        "bad_guests": _("Please choose between 1 and 5 places."),
        "contact_needed": _("Please allow the organiser to see your e-mail address, otherwise they cannot reach you."),
        "not_going": _("You are not signed up for this meeting."),
        "forbidden": _("This is not available to you."),
        "not_editable": _("This meeting can no longer be edited."),
        "capacity_below_going": _("More people are already signed up than the number of places you entered."),
        "nobody": _("Please choose at least one member to invite."),
        "too_many_invites": _("You can invite up to 20 members to one meeting."),
    }
    return messages.get(code, _("Something went wrong. Please try again."))


def _file_error(code):
    messages = {
        "empty": _("The file is empty."),
        "too_large": _("The file is too large."),
        "too_large_pixels": _("The picture has too many pixels."),
        "not_an_image": _("This file is not a picture."),
        "bad_format": _("Please use a JPG, PNG or WebP picture."),
    }
    return messages.get(code, _("The file could not be saved."))


def _visible_to(user, event):
    """Открытые встречи видны всем верифицированным; остальные — организатору, администратору и записавшимся."""
    if event.status == "open" or event.organizer_id == user.id or user.is_admin:
        return True
    return event.status == "cancelled" and any(
        r.user_id == user.id and r.status == "going" for r in event.rsvps)


def _form_context(event, errors):
    return dict(event=event, errors=errors, form=request.form, fields=EVENT_FIELDS, types=EVENT_TYPES,
                formats=EVENT_FORMATS, image_hint=IMAGE_HINT, venue_hint=VENUE_HINT, online_hint=ONLINE_HINT)


def _apply_image(event):
    cfg = current_app.config
    return attach_files(event, current_app.extensions["media"], f"events/{event.id}", request.files.get("image"),
                        None, bool(request.form.get("remove_image")), False,
                        attrs=("image_key", "image_key", "image_key"),  # PDF не используется
                        image_max_bytes=cfg["MEDIA_MAX_BYTES"], doc_max_bytes=cfg["DECK_MAX_BYTES"])


def _view(event, now):
    left = seats_left(event)
    return {"event": event, "seats_left": left, "going": going_seats(event.id), "price": price_text(event),
            "is_past": event.ends_at < now, "is_full": left == 0}


@bp.route("/")
@requires_feature("community.read")
def index():
    now = utcnow()
    scope = request.args.get("scope", "upcoming")
    kind = request.args.get("type", "")
    query = Event.query.filter(Event.status == "open")
    if kind in EVENT_TYPES:
        query = query.filter(Event.event_type == kind)
    if scope == "past":
        query = query.filter(Event.ends_at < now).order_by(Event.starts_at.desc())
    else:
        scope = "upcoming"
        query = query.filter(Event.ends_at >= now).order_by(Event.starts_at)
    invited = (Event.query.join(EventInvite).filter(EventInvite.user_id == current_user.id, Event.status == "open",
                                                    Event.ends_at >= now).order_by(Event.starts_at).all())
    return render_template("modules/events_list.html", items=[_view(e, now) for e in query.limit(100)],
                           invited=[_view(e, now) for e in invited], scope=scope, kind=kind, types=EVENT_TYPES,
                           can_create=has_feature(current_user, "events.create"))


@bp.route("/mine")
@requires_feature("community.read")
def mine():
    now = utcnow()
    organised = Event.query.filter_by(organizer_id=current_user.id).order_by(Event.starts_at.desc()).all()
    going = (Event.query.join(EventRsvp).filter(EventRsvp.user_id == current_user.id, EventRsvp.status == "going")
             .order_by(Event.starts_at).all())
    return render_template("modules/events_mine.html", organised=[_view(e, now) for e in organised],
                           going=[_view(e, now) for e in going],
                           can_create=has_feature(current_user, "events.create"))


@bp.route("/new", methods=["GET", "POST"])
@requires_feature("events.create")
def new():
    if request.method == "POST":
        data, errors = parse_event_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/events_form.html", **_form_context(None, errors))
        event = create_event(current_user, data)
        try:
            obsolete = _apply_image(event)
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/events_form.html", **_form_context(None, []))
        db.session.commit()
        for key in obsolete:
            current_app.extensions["media"].delete(key)
        flash(_("Your meeting is published.") if event.status == "open"
              else _("Your meeting is sent for review. We will publish it after the check."), "success")
        return redirect(url_for("events.detail", event_id=event.id))
    return render_template("modules/events_form.html", **_form_context(None, []))


@bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
@requires_feature("events.create")
def edit(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    if event.organizer_id != current_user.id:
        abort(404)
    if request.method == "POST":
        data, errors = parse_event_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/events_form.html", **_form_context(event, errors))
        try:
            edit_event(current_user, event, data)
            obsolete = _apply_image(event)
        except EventError as exc:
            db.session.rollback()
            flash(_error_message(exc.code), "error")
            return render_template("modules/events_form.html", **_form_context(event, []))
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/events_form.html", **_form_context(event, []))
        db.session.commit()
        for key in obsolete:
            current_app.extensions["media"].delete(key)
        flash(_("Your changes are saved.") if event.status == "open"
              else _("Your changes are sent for review."), "success")
        return redirect(url_for("events.detail", event_id=event.id))
    return render_template("modules/events_form.html", **_form_context(event, []))


@bp.route("/<int:event_id>")
@requires_feature("community.read")
def detail(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    if not _visible_to(current_user, event):
        abort(404)
    now = utcnow()
    view = _view(event, now)
    is_organizer = event.organizer_id == current_user.id
    my_rsvp = None if is_organizer else EventRsvp.query.filter_by(event_id=event.id, user_id=current_user.id,
                                                                  status="going").first()
    can_join = (has_feature(current_user, "events.join") and event.status == "open" and not view["is_past"]
                and not is_organizer and my_rsvp is None)
    # Адреса гостей видит только организатор и только тех, кто разрешил; ссылку на онлайн-встречу — записавшиеся
    roster = [(r.user.first_name, r.guests, r.user.email if r.share_contact else None)
              for r in event.rsvps if r.status == "going"] if is_organizer else []
    names = [r.user.first_name for r in event.rsvps if r.status == "going"]
    show_online = event.online_url and (is_organizer or my_rsvp is not None or current_user.is_admin)
    return render_template(
        "modules/events_detail.html", view=view, is_organizer=is_organizer, my_rsvp=my_rsvp, can_join=can_join,
        roster=roster, names=names, show_online=show_online, max_guests=MAX_GUESTS,
        can_edit=is_organizer and event.status != "cancelled" and not view["is_past"],
        invitable=invitable_members(event) if is_organizer and event.status == "open" and not view["is_past"] else [],
        invited_count=len(event.invites),
        was_invited=any(i.user_id == current_user.id for i in event.invites))


@bp.route("/<int:event_id>/image")
@requires_feature("community.read")
def image(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    if not event.image_key or not _visible_to(current_user, event):
        abort(404)
    data = current_app.extensions["media"].read(event.image_key)
    if data is None:
        abort(404)
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(
        event.image_key.rsplit(".", 1)[-1], "application/octet-stream")
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.route("/<int:event_id>/calendar.ics")
@requires_feature("community.read")
def calendar(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    if event.status != "open" or not _visible_to(current_user, event):
        abort(404)
    response = Response(to_ics(event), mimetype="text/calendar")
    response.headers["Content-Disposition"] = f'attachment; filename="event-{event.id}.ics"'
    return response


@bp.route("/<int:event_id>/join", methods=["POST"])
@requires_feature("events.join")
def join_event(event_id):
    try:
        guests = int(request.form.get("guests", "1"))
    except ValueError:
        guests = 0
    try:
        join(current_user, event_id, guests, share_contact=bool(request.form.get("share_contact")))
        db.session.commit()
    except EventError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
    else:
        flash(_("You are signed up. See you there!"), "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/leave", methods=["POST"])
@requires_feature("events.join")
def leave_event(event_id):
    try:
        leave(current_user, event_id)
        db.session.commit()
    except EventError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
    else:
        flash(_("Your sign-up is cancelled."), "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/invite", methods=["POST"])
@requires_feature("events.create")
def invite(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    if event.organizer_id != current_user.id:
        abort(404)
    ids = [int(v) for v in request.form.getlist("members") if v.isdigit()]
    try:
        invited = invite_members(current_user, event, ids)
        db.session.commit()
    except EventError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
    else:
        notify_invited(event, invited)  # письма после commit: сбой почты не отменяет приглашения
        flash(_("The invitations are sent."), "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/cancel", methods=["POST"])
@requires_feature("community.read")
def cancel(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    if event.organizer_id != current_user.id:
        abort(404)
    try:
        attendees = cancel_event(current_user, event)
        db.session.commit()
    except EventError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("events.detail", event_id=event_id))
    notify_cancelled(event, attendees)
    flash(_("The meeting is cancelled. Everyone who signed up is informed."), "success")
    return redirect(url_for("events.mine"))
