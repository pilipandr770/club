from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...miniclub_guide import CLUB_FIELDS, DISCLAIMER_SHORT, IMAGE_HINT, RULES
from ...models import MiniClub, MiniClubMember
from ...models.miniclubs import JOIN_MODES, TOPICS
from ...services.entitlements import has_feature, requires_feature
from ...services.media import MediaError, attach_files
from ...services.miniclubs import (
    ANNOUNCEMENT_MAX,
    MiniClubError,
    announce,
    close_club,
    create_club,
    decide,
    edit_club,
    join,
    leave,
    member_count,
    notify_decision,
    notify_request,
    parse_club_form,
    places_left,
    remove_member,
    report_club,
    send_announcement,
)

bp = Blueprint("miniclubs", __name__, url_prefix="/miniclubs")


def _error_message(code):
    messages = {
        "not_found": _("This mini-club does not exist."),
        "not_open": _("This mini-club is not open right now."),
        "own_club": _("This is your own mini-club."),
        "already_joined": _("You are already a member or your request is pending."),
        "declined": _("The organiser has already answered your request."),
        "full": _("This mini-club has no places left."),
        "contact_needed": _("Please allow the organiser to see your e-mail address, otherwise they cannot reach you."),
        "not_member": _("You are not a member of this mini-club."),
        "forbidden": _("This is not available to you."),
        "not_editable": _("This mini-club can no longer be edited."),
        "already_answered": _("This request has already been answered."),
        "empty": _("Please write a message."),
        "too_long": _("The message is too long."),
        "too_soon": _("You can send one message to all members per day."),
        "nobody": _("There are no members yet."),
        "reason_short": _("Please describe the problem in a few words."),
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


def _my_row(club):
    return MiniClubMember.query.filter_by(club_id=club.id, user_id=current_user.id).first()


def _visible_to(club):
    if club.status == "open" or club.organizer_id == current_user.id or current_user.is_admin:
        return True
    row = _my_row(club)  # закрытый клуб видят его бывшие участники
    return club.status == "closed" and row is not None and row.status == "member"


def _form_context(club, errors):
    return dict(club=club, errors=errors, form=request.form, fields=CLUB_FIELDS, topics=TOPICS,
                join_modes=JOIN_MODES, image_hint=IMAGE_HINT, disclaimer=DISCLAIMER_SHORT)


def _apply_image(club):
    cfg = current_app.config
    return attach_files(club, current_app.extensions["media"], f"miniclubs/{club.id}", request.files.get("image"),
                        None, bool(request.form.get("remove_image")), False,
                        attrs=("image_key", "image_key", "image_key"),  # PDF не используется
                        image_max_bytes=cfg["MEDIA_MAX_BYTES"], doc_max_bytes=cfg["DECK_MAX_BYTES"])


def _card(club):
    return {"club": club, "members": member_count(club.id) + 1, "places_left": places_left(club)}


@bp.route("/")
@requires_feature("community.read")
def index():
    topic = request.args.get("topic", "")
    city = request.args.get("city", "").strip()
    query = MiniClub.query.filter(MiniClub.status == "open")
    if topic in TOPICS:
        query = query.filter(MiniClub.topic == topic)
    if city:
        query = query.filter(MiniClub.city.ilike(f"%{city[:60]}%"))
    clubs = query.order_by(MiniClub.created_at.desc()).limit(100).all()
    return render_template("modules/miniclubs_list.html", items=[_card(c) for c in clubs], topics=TOPICS,
                           topic=topic, city=city, disclaimer=DISCLAIMER_SHORT,
                           can_create=has_feature(current_user, "miniclubs.create"))


@bp.route("/rules")
@requires_feature("community.read")
def rules():
    return render_template("modules/miniclubs_rules.html", rules=RULES, disclaimer=DISCLAIMER_SHORT)


@bp.route("/mine")
@requires_feature("community.read")
def mine():
    organised = MiniClub.query.filter_by(organizer_id=current_user.id).order_by(MiniClub.created_at.desc()).all()
    rows = (MiniClubMember.query.filter(MiniClubMember.user_id == current_user.id,
                                        MiniClubMember.status.in_(("member", "pending")))
            .order_by(MiniClubMember.created_at.desc()).all())
    return render_template("modules/miniclubs_mine.html", organised=[_card(c) for c in organised],
                           joined=[(_card(r.club), r.status) for r in rows],
                           can_create=has_feature(current_user, "miniclubs.create"))


@bp.route("/new", methods=["GET", "POST"])
@requires_feature("miniclubs.create")
def new():
    if request.method == "POST":
        data, errors = parse_club_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/miniclubs_form.html", **_form_context(None, errors))
        club = create_club(current_user, data)
        try:
            obsolete = _apply_image(club)
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/miniclubs_form.html", **_form_context(None, []))
        db.session.commit()
        for key in obsolete:
            current_app.extensions["media"].delete(key)
        flash(_("Your mini-club is published.") if club.status == "open"
              else _("Your mini-club is sent for review. We will publish it after the check."), "success")
        return redirect(url_for("miniclubs.detail", club_id=club.id))
    return render_template("modules/miniclubs_form.html", **_form_context(None, []))


@bp.route("/<int:club_id>/edit", methods=["GET", "POST"])
@requires_feature("miniclubs.create")
def edit(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    if club.organizer_id != current_user.id:
        abort(404)
    if request.method == "POST":
        data, errors = parse_club_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/miniclubs_form.html", **_form_context(club, errors))
        try:
            edit_club(current_user, club, data)
            obsolete = _apply_image(club)
        except MiniClubError as exc:
            db.session.rollback()
            flash(_error_message(exc.code), "error")
            return render_template("modules/miniclubs_form.html", **_form_context(club, []))
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/miniclubs_form.html", **_form_context(club, []))
        db.session.commit()
        for key in obsolete:
            current_app.extensions["media"].delete(key)
        flash(_("Your changes are saved.") if club.status == "open"
              else _("Your changes are sent for review."), "success")
        return redirect(url_for("miniclubs.detail", club_id=club.id))
    return render_template("modules/miniclubs_form.html", **_form_context(club, []))


@bp.route("/<int:club_id>")
@requires_feature("community.read")
def detail(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    if not _visible_to(club):
        abort(404)
    is_organizer = club.organizer_id == current_user.id
    row = None if is_organizer else _my_row(club)
    members = [r for r in club.members if r.status == "member"]
    pending = [r for r in club.members if r.status == "pending"] if is_organizer else []
    can_join = (has_feature(current_user, "miniclubs.join") and club.status == "open" and not is_organizer
                and (row is None or row.status == "left"))
    return render_template(
        "modules/miniclubs_detail.html", card=_card(club), club=club, is_organizer=is_organizer, row=row,
        members=members, pending=pending, can_join=can_join, disclaimer=DISCLAIMER_SHORT,
        announcement_max=ANNOUNCEMENT_MAX, fields=CLUB_FIELDS)


@bp.route("/<int:club_id>/image")
@requires_feature("community.read")
def image(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    if not club.image_key or not _visible_to(club):
        abort(404)
    data = current_app.extensions["media"].read(club.image_key)
    if data is None:
        abort(404)
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(
        club.image_key.rsplit(".", 1)[-1], "application/octet-stream")
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


def _act(club_id, success_message, action):
    """Общий шаблон POST-действия: выполнить, сохранить, показать сообщение и вернуться на страницу клуба."""
    try:
        result = action()
        db.session.commit()
    except MiniClubError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("miniclubs.detail", club_id=club_id)), None
    if success_message:
        flash(success_message, "success")
    return redirect(url_for("miniclubs.detail", club_id=club_id)), result


@bp.route("/<int:club_id>/join", methods=["POST"])
@requires_feature("miniclubs.join")
def join_club(club_id):
    response, row = _act(club_id, None, lambda: join(current_user, club_id, request.form.get("message", ""),
                                                      bool(request.form.get("share_contact"))))
    if row is not None:
        club = row.club
        if row.status == "pending":
            notify_request(club, current_user)
            flash(_("Your request is sent to the organiser."), "success")
        else:
            flash(_("You are a member now."), "success")
    return response


@bp.route("/<int:club_id>/leave", methods=["POST"])
@requires_feature("miniclubs.join")
def leave_club(club_id):
    return _act(club_id, _("You left the mini-club."), lambda: leave(current_user, club_id))[0]


@bp.route("/requests/<int:row_id>/answer", methods=["POST"])
@requires_feature("miniclubs.create")
def answer(row_id):
    row = db.session.get(MiniClubMember, row_id) or abort(404)
    if row.club.organizer_id != current_user.id:
        abort(404)
    accept = request.form.get("answer") == "accept"
    response, _unused = _act(row.club_id, _("Your answer is saved."), lambda: decide(current_user, row, accept))
    if row.status in ("member", "declined"):
        notify_decision(row.club, row.user, accepted=row.status == "member")
    return response


@bp.route("/members/<int:row_id>/remove", methods=["POST"])
@requires_feature("miniclubs.create")
def remove(row_id):
    row = db.session.get(MiniClubMember, row_id) or abort(404)
    if row.club.organizer_id != current_user.id:
        abort(404)
    return _act(row.club_id, _("The member is removed."), lambda: remove_member(current_user, row))[0]


@bp.route("/<int:club_id>/announce", methods=["POST"])
@requires_feature("miniclubs.create")
def announce_view(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    if club.organizer_id != current_user.id:
        abort(404)
    text = request.form.get("text", "")
    response, recipients = _act(club_id, None, lambda: announce(current_user, club, text))
    if recipients:
        send_announcement(club, text, recipients)
        flash(_("Your message is sent to all members."), "success")
    return response


@bp.route("/<int:club_id>/report", methods=["POST"])
@requires_feature("community.read")
def report(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    if not _visible_to(club):
        abort(404)
    return _act(club_id, _("Thank you. Our team will look at it."),
                lambda: report_club(current_user, club, request.form.get("reason", "")))[0]


@bp.route("/<int:club_id>/close", methods=["POST"])
@requires_feature("community.read")
def close(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    if club.organizer_id != current_user.id:
        abort(404)
    response, _unused = _act(club_id, _("The mini-club is closed."), lambda: close_club(current_user, club))
    return response
