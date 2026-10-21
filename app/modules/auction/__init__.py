from datetime import timedelta

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...auction_guide import (
    DESCRIPTION_EXAMPLES,
    DESCRIPTION_HINT,
    DURATIONS,
    MODE_HELP,
    PHOTO_HINT,
    PICKUP_EXAMPLES,
    PICKUP_HINT,
    RESERVE_HELP,
    SNIPE_HELP,
)
from ...extensions import db
from ...models import Bid, Lot
from ...models.auction import CONDITIONS
from ...services.auction_engine import (
    AuctionError,
    cancel_lot,
    close_due_lots,
    confirm_received,
    counterparty,
    create_member_lot,
    current_price,
    edit_member_lot,
    finish_if_due,
    is_sold,
    mark_shipped,
    minimum_next_bid,
    next_drop_at,
    place_bid,
    reserve_met,
    validate_lot_input,
)
from ...services.auction_notify import notify_closed, notify_outbid
from ...services.audit import audit
from ...services.entitlements import requires_feature
from ...services.media import MediaError, attach_files
from ...utils import eur_to_cents, format_eur, utcnow

bp = Blueprint("auction", __name__, url_prefix="/auction")

MAX_BID_HISTORY = 25


def _error_message(code):
    messages = {
        "closed": _("This lot is closed."),
        "not_started": _("This lot has not started yet."),
        "bid_too_low": _("Your bid is below the minimum. Please raise it."),
        "price_too_low": _("The price has dropped below your offer. Please reload the page."),
        "not_found": _("This lot does not exist."),
        "own_lot": _("You cannot bid on your own lot."),
        "contact_needed": _("Please allow the seller to see your contact details if you win."),
        "forbidden": _("This is not available to you."),
        "not_sold": _("This lot has no buyer."),
        "not_editable": _("This lot can no longer be edited."),
        "has_bids": _("A lot with bids cannot be withdrawn."),
        "not_pending": _("This lot is not waiting for review."),
    }
    return messages.get(code, _("The bid could not be placed."))


def _file_error(code):
    messages = {
        "empty": _("The file is empty."),
        "too_large": _("The file is too large."),
        "too_large_pixels": _("The picture has too many pixels."),
        "not_an_image": _("This file is not a picture."),
        "bad_format": _("Please use a JPG, PNG or WebP picture."),
    }
    return messages.get(code, _("The file could not be saved."))


def _visible_to(user, lot):
    """Открытые и закрытые лоты видны участникам аукциона; на проверке и отклонённые — продавцу и администратору."""
    return lot.status in ("open", "closed") or lot.seller_id == user.id or user.is_admin


def _settle(lots):
    """Закрывает лоты, у которых вышло время, пока планировщик не успел. Письма уходят после commit."""
    now = utcnow()
    due = [lot for lot in lots if finish_if_due(lot, now)]
    if due:
        db.session.commit()
        for lot in due:
            notify_closed(lot)


def _view_model(lot, now):
    price = current_price(lot, now)
    is_open = lot.status == "open" and lot.opens_at <= now < lot.ends_at
    return {
        "lot": lot,
        "price": format_eur(price),
        "price_cents": price,
        "price_plain": f"{price // 100}.{price % 100:02d}",
        "is_open": is_open,
        "is_upcoming": lot.status == "open" and now < lot.opens_at,
        "is_descending": lot.mode == "descending",
        "min_bid": format_eur(minimum_next_bid(lot.start_price_cents, lot.current_price_cents, lot.step_cents,
                                               bool(lot.bids)) if lot.mode == "ascending" else price),
        "start": format_eur(lot.start_price_cents),
        "step": format_eur(lot.step_cents),
        "floor": format_eur(lot.floor_price_cents),
        "bid_count": len(lot.bids),
        "has_reserve": lot.reserve_price_cents is not None,
        "reserve_met": reserve_met(lot),
        "sold": is_sold(lot),
        "seller_name": lot.seller.first_name if lot.seller else None,
    }


def _bid_history(lot, viewer):
    """Ставки без имён: участники обозначены номерами по порядку появления; свои ставки помечены."""
    numbers, rows = {}, []
    for bid in lot.bids:
        numbers.setdefault(bid.user_id, len(numbers) + 1)
    for bid in reversed(lot.bids[-MAX_BID_HISTORY:]):
        rows.append({"bidder": numbers[bid.user_id], "mine": bid.user_id == viewer.id,
                     "amount": format_eur(bid.amount_cents), "at": bid.created_at})
    return rows


def _parse_form(form, existing=None):
    """Форма лота участника. Возвращает (данные, ошибки)."""
    errors = []
    data = {"title": form.get("title", "").strip(), "description": form.get("description", "").strip()[:3000],
            "mode": form.get("mode", ""), "pickup_info": form.get("pickup_info", "").strip()[:1000],
            "condition": form.get("condition") if form.get("condition") in CONDITIONS else None}
    if len(data["title"]) > 160:
        errors.append("title")
    if not data["description"]:
        errors.append("description")
    for field, key in (("start_price", "start_price_cents"), ("step", "step_cents")):
        try:
            data[key] = eur_to_cents(form.get(field, ""))
        except ValueError:
            errors.append(field)
    raw_reserve = form.get("reserve_price", "").strip()
    if raw_reserve and data["mode"] == "ascending":
        try:
            data["reserve_price_cents"] = eur_to_cents(raw_reserve)
        except ValueError:
            errors.append("reserve_price")
    if data["mode"] == "descending":
        try:
            data["floor_price_cents"] = eur_to_cents(form.get("floor_price", ""))
        except ValueError:
            errors.append("floor_price")
        try:
            data["drop_interval_seconds"] = int(form.get("drop_interval_minutes", "0")) * 60
        except ValueError:
            errors.append("drop_interval")
    try:
        days = int(form.get("duration_days", "7"))
        if days not in DURATIONS:
            raise ValueError
    except ValueError:
        days = 7
        errors.append("duration")
    now = utcnow()
    data["opens_at"], data["ends_at"] = now, now + timedelta(days=days)
    if not form.get("seller_consent"):
        errors.append("seller_consent")
    errors += [code for code in validate_lot_input(data, member=True) if code not in errors]
    return data, errors


def _form_context(lot, errors):
    return dict(lot=lot, errors=errors, form=request.form, conditions=CONDITIONS, durations=DURATIONS,
                description_hint=DESCRIPTION_HINT, description_examples=DESCRIPTION_EXAMPLES,
                pickup_hint=PICKUP_HINT, pickup_examples=PICKUP_EXAMPLES, photo_hint=PHOTO_HINT,
                mode_help=MODE_HELP, reserve_help=RESERVE_HELP, snipe_help=SNIPE_HELP)


def _apply_image(lot):
    cfg = current_app.config
    return attach_files(lot, current_app.extensions["media"], f"lots/{lot.id}", request.files.get("image"), None,
                        bool(request.form.get("remove_image")), False,
                        attrs=("image_key", "image_key", "pickup_info"),  # PDF не используется: передаём None
                        image_max_bytes=cfg["MEDIA_MAX_BYTES"], doc_max_bytes=cfg["DECK_MAX_BYTES"])


@bp.route("/")
@requires_feature("auction.access")
def index():
    _settle(Lot.query.filter(Lot.status == "open", Lot.ends_at <= utcnow()).all())
    now = utcnow()
    state = request.args.get("state", "open")
    query = Lot.query
    if state == "closed":
        query = query.filter(Lot.status == "closed").order_by(Lot.ends_at.desc())
    elif state == "upcoming":
        query = query.filter(Lot.status == "open", Lot.opens_at > now).order_by(Lot.opens_at)
    else:
        state = "open"
        query = query.filter(Lot.status == "open", Lot.opens_at <= now).order_by(Lot.ends_at)
    return render_template("modules/auction_list.html", items=[_view_model(lot, now) for lot in query.limit(100)],
                           state=state, now=now)


@bp.route("/mine")
@requires_feature("auction.access")
def mine():
    _settle(Lot.query.filter(Lot.status == "open", Lot.ends_at <= utcnow()).all())
    now = utcnow()
    selling = Lot.query.filter_by(seller_id=current_user.id).order_by(Lot.created_at.desc()).all()
    bid_lot_ids = {row[0] for row in db.session.query(Bid.lot_id).filter_by(user_id=current_user.id).all()}
    bidding = Lot.query.filter(Lot.id.in_(bid_lot_ids)).order_by(Lot.ends_at.desc()).all() if bid_lot_ids else []
    leading = {lot.id for lot in bidding if lot.bids and lot.bids[-1].user_id == current_user.id}
    return render_template("modules/auction_mine.html", selling=[_view_model(lot, now) for lot in selling],
                           bidding=[_view_model(lot, now) for lot in bidding], leading=leading)


@bp.route("/new", methods=["GET", "POST"])
@requires_feature("auction.access")
def new():
    if request.method == "POST":
        data, errors = _parse_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/auction_form.html", **_form_context(None, errors))
        lot = create_member_lot(current_user, data)
        try:
            obsolete = _apply_image(lot)
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/auction_form.html", **_form_context(None, []))
        db.session.commit()
        for key in obsolete:
            current_app.extensions["media"].delete(key)
        flash(_("Your lot is sent for review. We will publish it after the check."), "success")
        return redirect(url_for("auction.detail", lot_id=lot.id))
    return render_template("modules/auction_form.html", **_form_context(None, []))


@bp.route("/<int:lot_id>/edit", methods=["GET", "POST"])
@requires_feature("auction.access")
def edit(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    if lot.seller_id != current_user.id:
        abort(404)
    if request.method == "POST":
        data, errors = _parse_form(request.form)
        if errors:
            flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")
            return render_template("modules/auction_form.html", **_form_context(lot, errors))
        try:
            edit_member_lot(current_user, lot, data)
            obsolete = _apply_image(lot)
        except AuctionError as exc:
            db.session.rollback()
            flash(_error_message(exc.code), "error")
            return redirect(url_for("auction.detail", lot_id=lot.id))
        except MediaError as exc:
            db.session.rollback()
            flash(_file_error(exc.code), "error")
            return render_template("modules/auction_form.html", **_form_context(lot, []))
        db.session.commit()
        for key in obsolete:
            current_app.extensions["media"].delete(key)
        flash(_("Your changes are sent for review."), "success")
        return redirect(url_for("auction.detail", lot_id=lot.id))
    if lot.status not in ("pending", "rejected") or lot.bids:
        flash(_error_message("not_editable"), "error")
        return redirect(url_for("auction.detail", lot_id=lot.id))
    return render_template("modules/auction_form.html", **_form_context(lot, []))


@bp.route("/<int:lot_id>")
@requires_feature("auction.access")
def detail(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    if not _visible_to(current_user, lot):
        abort(404)
    _settle([lot])
    now = utcnow()
    item = _view_model(lot, now)
    is_seller = lot.seller_id == current_user.id
    leader = bool(lot.bids) and lot.bids[-1].user_id == current_user.id
    contact = counterparty(current_user, lot)
    return render_template(
        "modules/auction_detail.html", item=item, is_seller=is_seller, leader=leader, contact=contact,
        history=_bid_history(lot, current_user), is_winner=lot.winner_id == current_user.id,
        can_bid=item["is_open"] and not is_seller, snipe_help=SNIPE_HELP, now=now,
        can_withdraw=(is_seller and lot.status in ("open", "pending") and not lot.bids),
        can_edit=(is_seller and lot.status in ("pending", "rejected") and not lot.bids),
        drop_at=next_drop_at(lot, now) if lot.status == "open" else None)


@bp.route("/<int:lot_id>/state")
@requires_feature("auction.access")
def state(lot_id):
    """Лёгкое состояние лота для обновления страницы без перезагрузки."""
    lot = db.session.get(Lot, lot_id) or abort(404)
    if not _visible_to(current_user, lot):
        abort(404)
    _settle([lot])
    now = utcnow()
    item = _view_model(lot, now)
    drop = next_drop_at(lot, now) if lot.status == "open" else None
    return jsonify(
        status=lot.status, price=item["price"], min_bid=item["min_bid"], bids=item["bid_count"],
        leader=bool(lot.bids) and lot.bids[-1].user_id == current_user.id,
        seconds_left=max(0, int((lot.ends_at - now).total_seconds())) if lot.status == "open" else 0,
        seconds_to_start=max(0, int((lot.opens_at - now).total_seconds())),
        seconds_to_drop=max(0, int((drop - now).total_seconds())) if drop else None,
        reserve_met=item["reserve_met"], sold=item["sold"], price_plain=item["price_plain"])


@bp.route("/<int:lot_id>/image")
@requires_feature("auction.access")
def image(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    if not lot.image_key or not _visible_to(current_user, lot):
        abort(404)
    data = current_app.extensions["media"].read(lot.image_key)
    if data is None:
        abort(404)
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(
        lot.image_key.rsplit(".", 1)[-1], "application/octet-stream")
    response = Response(data, mimetype=mime)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.route("/<int:lot_id>/bid", methods=["POST"])
@requires_feature("auction.access")
def bid(lot_id):
    try:
        amount_cents = eur_to_cents(request.form.get("amount", ""))
    except ValueError:
        flash(_("Please enter a valid amount."), "error")
        return redirect(url_for("auction.detail", lot_id=lot_id))
    if not request.form.get("share_contact"):
        flash(_error_message("contact_needed"), "error")
        return redirect(url_for("auction.detail", lot_id=lot_id))

    try:
        placed = place_bid(lot_id, current_user, amount_cents, now=utcnow(), share_contact=True)
        db.session.commit()
    except AuctionError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("auction.detail", lot_id=lot_id))

    lot = placed.lot
    if lot.status == "closed":  # понижение: первая же покупка закрывает лот
        notify_closed(lot)
        flash(_("You bought the lot. The seller and you can now arrange the handover."), "success")
    else:
        notify_outbid(getattr(placed, "previous_leader_id", None), lot)
        flash(_("Your bid is placed."), "success")
    return redirect(url_for("auction.detail", lot_id=lot_id))


@bp.route("/<int:lot_id>/cancel", methods=["POST"])
@requires_feature("auction.access")
def cancel(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    if lot.seller_id != current_user.id:
        abort(404)
    try:
        cancel_lot(current_user, lot)
        db.session.commit()
    except AuctionError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("auction.detail", lot_id=lot_id))
    flash(_("The lot is withdrawn."), "success")
    return redirect(url_for("auction.mine"))


def _handover(action, success_message):
    def view(lot_id):
        lot = db.session.get(Lot, lot_id) or abort(404)
        try:
            action(current_user, lot)
            db.session.commit()
        except AuctionError as exc:
            db.session.rollback()
            flash(_error_message(exc.code), "error")
        else:
            flash(success_message, "success")
        return redirect(url_for("auction.detail", lot_id=lot_id))
    return view


bp.add_url_rule("/<int:lot_id>/shipped", "shipped", requires_feature("auction.access")(
    _handover(mark_shipped, _("Marked as handed over."))), methods=["POST"])
bp.add_url_rule("/<int:lot_id>/received", "received", requires_feature("auction.access")(
    _handover(confirm_received, _("Thank you. The deal is complete."))), methods=["POST"])
