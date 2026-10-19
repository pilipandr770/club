from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required

from config.plans import PLANS
from ...extensions import db
from ...models import AuditLog, Consent, Lot, User
from ...models.user import ROLES, VERIFICATION_STATUSES
from ...services.audit import audit
from ...models import Startup
from ...services import startups as startup_service
from ...services.auction_engine import cancel_lot, create_lot, validate_lot_input
from ...utils import eur_to_cents, format_eur, local_to_utc, utcnow

bp = Blueprint("admin", __name__, url_prefix="/admin")

STATUSES = ("active", "blocked")
VERIFICATION_REASONS = (
    "", "manual_override", "needs_review", "sanctioned_country", "underage",
    "requires_input", "canceled", "attempts_exhausted",
)


def admin_required(view):
    """Админ-зона: для не-админов адрес отвечает 404, чтобы не выдавать, что раздел существует."""

    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(404)
        return view(*args, **kwargs)

    return wrapper


@bp.route("/")
@admin_required
def dashboard():
    by_verification = {
        status: User.query.filter_by(verification_status=status).count()
        for status in VERIFICATION_STATUSES
    }
    by_plan = {plan: User.query.filter_by(plan=plan).count() for plan in PLANS}
    return render_template(
        "admin/dashboard.html",
        users_total=User.query.count(),
        by_verification=by_verification,
        by_plan=by_plan,
        lots_open=Lot.query.filter_by(status="open").count(),
        plans=PLANS,
    )


@bp.route("/users")
@admin_required
def users():
    query = request.args.get("q", "").strip().lower()
    items = User.query
    if query:
        items = items.filter(User.email.ilike(f"%{query}%"))
    items = items.order_by(User.created_at.desc()).limit(200).all()
    return render_template("admin/users.html", users=items, q=query, plans=PLANS)


def _change_list(user, form):
    """Применяет изменения формы к участнику и возвращает список того, что изменилось."""
    changes = []

    plan = form.get("plan", user.plan)
    if plan in PLANS and plan != user.plan:
        changes.append(f"plan {user.plan}->{plan}")
        user.plan = plan

    role = form.get("role", user.role)
    if role in ROLES and role != user.role:
        changes.append(f"role {user.role}->{role}")
        user.role = role

    status = form.get("status", user.status)
    if status in STATUSES and status != user.status and not (status == "blocked" and user.id == current_user.id):
        changes.append(f"status {user.status}->{status}")
        user.status = status

    is_admin = bool(form.get("is_admin"))
    if is_admin != user.is_admin and not (user.id == current_user.id and not is_admin):
        changes.append(f"is_admin {user.is_admin}->{is_admin}")
        user.is_admin = is_admin

    vstatus = form.get("verification_status", user.verification_status)
    reason = form.get("verification_reason", "")
    if vstatus in VERIFICATION_STATUSES and (vstatus != user.verification_status or reason != (user.verification_reason or "")):
        if reason not in VERIFICATION_REASONS:
            reason = ""
        changes.append(f"verification {user.verification_status}->{vstatus} ({reason or '-'})")
        if vstatus == "verified" and user.verification_status != "verified":
            user.verified_at = utcnow()
        if vstatus != "verified":
            user.verified_at = None
        user.verification_status = vstatus
        user.verification_reason = reason or None

    return changes


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user_detail(user_id):
    user = db.session.get(User, user_id) or abort(404)

    if request.method == "POST":
        changes = _change_list(user, request.form)
        if changes:
            audit("admin.user_updated", target=f"user:{user.id}", details="; ".join(changes))
            db.session.commit()
            flash(_("Changes are saved."), "success")
        else:
            flash(_("Nothing has changed."), "info")
        return redirect(url_for("admin.user_detail", user_id=user.id))

    history = (AuditLog.query.filter_by(target=f"user:{user.id}")
               .order_by(AuditLog.created_at.desc()).limit(50).all())
    consents = Consent.query.filter_by(user_id=user.id).order_by(Consent.created_at).all()
    return render_template("admin/user_detail.html", user=user, plans=PLANS, roles=ROLES,
                           statuses=STATUSES, verification_statuses=VERIFICATION_STATUSES,
                           reasons=VERIFICATION_REASONS, history=history, consents=consents)


@bp.route("/lots", methods=["GET", "POST"])
@admin_required
def lots():
    if request.method == "POST":
        data, errors = _parse_lot_form(request.form)
        if not errors:
            errors = validate_lot_input(data)
        if errors:
            flash(_("Please check the lot fields: %(fields)s", fields=", ".join(errors)), "error")
        else:
            create_lot(current_user, data)
            db.session.commit()
            flash(_("The lot is created."), "success")
        return redirect(url_for("admin.lots"))

    items = Lot.query.order_by(Lot.created_at.desc()).limit(200).all()
    return render_template("admin/lots.html", lots=items, format_eur=format_eur)


@bp.route("/lots/<int:lot_id>/cancel", methods=["POST"])
@admin_required
def cancel(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    cancel_lot(current_user, lot)
    db.session.commit()
    flash(_("The lot is closed."), "success")
    return redirect(url_for("admin.lots"))


def _parse_lot_form(form):
    """Разбирает форму лота. Ошибки ввода возвращаются списком полей, а не исключением."""
    errors = []
    data = {
        "title": form.get("title", ""),
        "description": form.get("description", ""),
        "mode": form.get("mode", ""),
    }
    try:
        data["start_price_cents"] = eur_to_cents(form.get("start_price", ""))
    except ValueError:
        errors.append("start_price")
    try:
        data["step_cents"] = eur_to_cents(form.get("step", ""))
    except ValueError:
        errors.append("step")
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
        data["opens_at"] = local_to_utc(form.get("opens_at", ""))
        data["ends_at"] = local_to_utc(form.get("ends_at", ""))
    except ValueError:
        errors.append("times")
    return data, errors


@bp.route("/audit")
@admin_required
def audit_log():
    rows = (db.session.query(AuditLog, User.email)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .order_by(AuditLog.created_at.desc()).limit(200).all())
    return render_template("admin/audit.html", rows=rows)


@bp.route("/startups")
@admin_required
def startups():
    status = request.args.get("status", "pending")
    query = Startup.query
    if status in ("pending", "approved", "rejected"):
        query = query.filter_by(status=status)
    items = query.order_by(Startup.created_at.desc()).limit(200).all()
    return render_template("admin/startups.html", items=items, status=status,
                           format_eur=format_eur)


@bp.route("/startups/<int:startup_id>/review", methods=["POST"])
@admin_required
def review_startup(startup_id):
    startup = db.session.get(Startup, startup_id) or abort(404)
    if request.form.get("decision") == "approve":
        startup_service.approve(current_user, startup)
        flash(_("The startup is published."), "success")
    elif request.form.get("decision") == "reject":
        startup_service.reject(current_user, startup, request.form.get("reason", ""))
        flash(_("The startup is rejected."), "success")
    else:
        flash(_("Nothing has changed."), "info")
        return redirect(url_for("admin.startups"))
    db.session.commit()
    return redirect(url_for("admin.startups", status="pending"))

