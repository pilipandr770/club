from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...models import InvestorInterest, Startup
from ...models.startups import CATEGORIES, STAGES
from ...services.entitlements import has_feature, requires_feature
from ...services.startups import (
    StartupError,
    create_startup,
    express_interest,
    parse_startup_form,
    respond_to_interest,
    update_startup,
    visible_contact,
)
from ...utils import format_eur

bp = Blueprint("startups", __name__, url_prefix="/startups")


def _error_message(code):
    messages = {
        "not_available": _("This startup is not open for interest right now."),
        "own_startup": _("You cannot express interest in your own startup."),
        "already_sent": _("You have already sent your interest in this startup."),
        "already_answered": _("You have already answered this interest."),
        "forbidden": _("This is not available to you."),
    }
    return messages.get(code, _("Something went wrong. Please try again."))


def _field_errors(fields):
    return _("Please check these fields: %(fields)s", fields=", ".join(fields))


@bp.route("/")
@requires_feature("community.read")
def index():
    query = Startup.query.filter_by(status="approved")
    category = request.args.get("category", "")
    stage = request.args.get("stage", "")
    if category in CATEGORIES:
        query = query.filter_by(category=category)
    if stage in STAGES:
        query = query.filter_by(stage=stage)
    items = query.order_by(Startup.approved_at.desc()).limit(200).all()
    return render_template("modules/startups_list.html", items=items, categories=CATEGORIES,
                           stages=STAGES, category=category, stage=stage)


@bp.route("/<int:startup_id>")
@requires_feature("community.read")
def detail(startup_id):
    startup = db.session.get(Startup, startup_id) or abort(404)
    is_owner = startup.owner_id == current_user.id
    if startup.status != "approved" and not is_owner:
        abort(404)
    my_interest = None
    if not is_owner:
        my_interest = InvestorInterest.query.filter_by(startup_id=startup.id, investor_id=current_user.id).first()
    return render_template("modules/startups_detail.html", startup=startup, is_owner=is_owner,
                           my_interest=my_interest, contact=visible_contact(current_user, startup),
                           can_interest=has_feature(current_user, "startups.interest"),
                           funding=format_eur(startup.funding_goal_cents))


@bp.route("/<int:startup_id>/interest", methods=["POST"])
@requires_feature("startups.interest")
def interest(startup_id):
    startup = db.session.get(Startup, startup_id) or abort(404)
    try:
        express_interest(current_user, startup, request.form.get("message", ""))
        db.session.commit()
    except StartupError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("startups.detail", startup_id=startup.id))
    flash(_("Your interest is sent. The founder decides whether to share contact details."), "success")
    return redirect(url_for("startups.detail", startup_id=startup.id))


@bp.route("/mine")
@requires_feature("startups.publish")
def mine():
    items = Startup.query.filter_by(owner_id=current_user.id).order_by(Startup.created_at.desc()).all()
    incoming = (InvestorInterest.query.join(Startup)
                .filter(Startup.owner_id == current_user.id)
                .order_by(InvestorInterest.created_at.desc()).all())
    return render_template("modules/startups_mine.html", items=items, incoming=incoming)


@bp.route("/new", methods=["GET", "POST"])
@requires_feature("startups.publish")
def new():
    errors = []
    if request.method == "POST":
        data, errors = parse_startup_form(request.form)
        if not errors:
            create_startup(current_user, data)
            db.session.commit()
            flash(_("Your startup is sent for review. We will let you know when it is published."), "success")
            return redirect(url_for("startups.mine"))
        flash(_field_errors(errors), "error")
    return render_template("modules/startups_form.html", startup=None, form=request.form,
                           categories=CATEGORIES, stages=STAGES, errors=errors)


@bp.route("/<int:startup_id>/edit", methods=["GET", "POST"])
@requires_feature("startups.publish")
def edit(startup_id):
    startup = db.session.get(Startup, startup_id) or abort(404)
    if startup.owner_id != current_user.id:
        abort(404)
    errors = []
    if request.method == "POST":
        data, errors = parse_startup_form(request.form)
        if not errors:
            update_startup(current_user, startup, data)
            db.session.commit()
            flash(_("Your changes are sent for review."), "success")
            return redirect(url_for("startups.mine"))
        flash(_field_errors(errors), "error")
    return render_template("modules/startups_form.html", startup=startup, form=request.form,
                           categories=CATEGORIES, stages=STAGES, errors=errors)


@bp.route("/interests/<int:interest_id>/respond", methods=["POST"])
@requires_feature("startups.publish")
def respond(interest_id):
    interest = db.session.get(InvestorInterest, interest_id) or abort(404)
    accept = request.form.get("answer") == "accept"
    try:
        respond_to_interest(current_user, interest, accept)
        db.session.commit()
    except StartupError as exc:
        db.session.rollback()
        flash(_error_message(exc.code), "error")
        return redirect(url_for("startups.mine"))
    flash(_("Your answer is saved."), "success")
    return redirect(url_for("startups.mine"))
