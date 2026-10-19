from flask import Blueprint, render_template
from flask_babel import gettext as _

from ...services.entitlements import requires_feature

bp = Blueprint("auction", __name__, url_prefix="/auction")


@bp.route("/")
@requires_feature("auction.access")
def index():
    return render_template("modules/stub.html", title=_("Auction"))
