from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import Service, ServiceInquiry
from ...services.catalog import CatalogError, request_service
from ...services.entitlements import requires_feature

bp = Blueprint("services", __name__, url_prefix="/services")


def _error_message(code):
    messages = {
        "not_available": _("This service is not available right now."),
        "empty_message": _("Please write a short message to the provider."),
    }
    return messages.get(code, _("Something went wrong. Please try again."))


def _law_tool_country(service):
    """Код страны, если это услуга-поиск по законам и корпус для неё загружен; иначе None."""
    from ...services.catalog_defaults import LAW_SERVICES
    from ...services.tools import laws as laws_tool

    code = LAW_SERVICES.get(service.slug)
    return code if code and code in laws_tool.available_jurisdictions() else None


@bp.route("/")
@requires_feature("services.use")
def index():
    items = Service.query.filter_by(active=True).order_by(Service.sort, Service.title).all()
    return render_template("modules/services_list.html", items=items)


@bp.route("/mine")
@requires_feature("services.use")
def mine():
    inquiries = (ServiceInquiry.query.filter_by(user_id=current_user.id)
                 .order_by(ServiceInquiry.created_at.desc()).all())
    return render_template("modules/services_mine.html", inquiries=inquiries)


@bp.route("/<slug>", methods=["GET", "POST"])
@requires_feature("services.use")
def detail(slug):
    service = Service.query.filter_by(slug=slug).first() or abort(404)
    if not service.active:
        abort(404)
    if request.method == "POST":
        try:
            request_service(current_user, service, request.form.get("message", ""),
                            share_contact=bool(request.form.get("share_contact")))
            db.session.commit()
        except CatalogError as exc:
            db.session.rollback()
            flash(_error_message(exc.code), "error")
            return redirect(url_for("services.detail", slug=slug))
        flash(_("Your request is sent to the provider."), "success")
        return redirect(url_for("services.mine"))
    return render_template("modules/service_detail.html", service=service, tool_jurisdiction=_law_tool_country(service))


from . import tools  # noqa: E402,F401  (маршруты инструментов)
