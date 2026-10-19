from flask import Blueprint, render_template
from flask_babel import gettext as _

from ...services.entitlements import requires_feature

bp = Blueprint("startups", __name__, url_prefix="/startups")


@bp.route("/")
@requires_feature("startups.publish")
def index():
    return render_template("modules/stub.html", title=_("Startups for investors"))
