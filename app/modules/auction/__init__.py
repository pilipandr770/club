from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Lot
from ...services.auction_engine import AuctionError, current_price, place_bid
from ...services.entitlements import requires_feature
from ...utils import eur_to_cents, format_eur, utcnow

bp = Blueprint("auction", __name__, url_prefix="/auction")


def _error_message(code):
    messages = {
        "closed": _("This lot is closed."),
        "not_started": _("This lot has not started yet."),
        "bid_too_low": _("Your bid is below the minimum. Please raise it."),
        "price_too_low": _("The price has dropped below your offer. Please reload the page."),
        "not_found": _("This lot does not exist."),
    }
    return messages.get(code, _("The bid could not be placed."))


def _view_model(lot, now):
    price = current_price(lot, now)
    return {
        "lot": lot,
        "price": format_eur(price),
        "price_cents": price,
        "price_plain": f"{price // 100}.{price % 100:02d}",
        "is_open": lot.status == "open" and lot.opens_at <= now < lot.ends_at,
        "is_descending": lot.mode == "descending",
        "min_bid": format_eur(price + lot.step_cents if lot.mode == "ascending" and lot.bids else price),
        "start": format_eur(lot.start_price_cents),
        "step": format_eur(lot.step_cents),
        "floor": format_eur(lot.floor_price_cents),
    }


@bp.route("/")
@requires_feature("auction.access")
def index():
    now = utcnow()
    lots = Lot.query.order_by(Lot.ends_at.desc()).limit(100).all()
    return render_template("modules/auction_list.html",
                           items=[_view_model(lot, now) for lot in lots])


@bp.route("/<int:lot_id>")
@requires_feature("auction.access")
def detail(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    return render_template("modules/auction_detail.html", item=_view_model(lot, utcnow()))


@bp.route("/<int:lot_id>/bid", methods=["POST"])
@requires_feature("auction.access")
def bid(lot_id):
    try:
        amount_cents = eur_to_cents(request.form.get("amount", ""))
    except ValueError:
        flash(_("Please enter a valid amount."), "error")
        return redirect(url_for("auction.detail", lot_id=lot_id))

    try:
        place_bid(lot_id, current_user, amount_cents, now=utcnow())
        db.session.commit()
    except AuctionError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("auction.detail", lot_id=lot_id))

    flash(_("Your bid is placed."), "success")
    return redirect(url_for("auction.detail", lot_id=lot_id))
