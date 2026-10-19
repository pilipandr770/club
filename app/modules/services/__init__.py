from flask import Blueprint, render_template
from flask_babel import gettext as _

from ...services.entitlements import requires_feature

bp = Blueprint("services", __name__, url_prefix="/services")


@bp.route("/")
@requires_feature("services.use")
def index():
    return render_template("modules/stub.html", title=_("Services"))
