from flask import Blueprint, render_template
from flask_babel import gettext as _

from ...services.entitlements import requires_feature

bp = Blueprint("travel", __name__, url_prefix="/travel")


@bp.route("/")
@requires_feature("travel.book")
def index():
    return render_template("modules/stub.html", title=_("Travel"))
