import json
import logging

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required, logout_user

from ...extensions import db, limiter
from ...modules import enabled_modules
from ...services.audit import audit
from ...services.consents import latest_consent
from ...services.gdpr import delete_user, export_user
from ...services.verification import VerificationNotAllowed, reason_message, start_verification
from ..auth import send_confirmation

bp = Blueprint("member", __name__, url_prefix="/app")
log = logging.getLogger(__name__)


@bp.route("/")
@login_required
def dashboard():
    from ...services.entitlements import has_feature
    from config.plans import PLANS, DEFAULT_PLAN

    modules = [
        {"spec": spec, "available": has_feature(current_user, spec.feature)}
        for spec in enabled_modules(current_app.config["ENABLED_MODULES"])
    ]
    plan = PLANS.get(current_user.plan, PLANS[DEFAULT_PLAN])
    return render_template(
        "member/dashboard.html",
        reason=reason_message(current_user.verification_reason),
        modules=modules,
        plan_name=plan["name"],
        publication_decided=latest_consent(current_user, "directory") is not None,
    )


@bp.route("/verify", methods=["POST"])
@login_required
def verify():
    try:
        url = start_verification(current_user)
    except VerificationNotAllowed as exc:
        flash(reason_message(exc.reason) or _("Verification cannot be started right now."), "error")
        return redirect(url_for("member.dashboard"))
    except Exception:
        # Провайдер недоступен или ключ не задан: сообщаем участнику, детали — в лог
        db.session.rollback()
        log.exception("Identitätsprüfung konnte nicht gestartet werden")
        flash(_("Verification cannot be started right now."), "error")
        return redirect(url_for("member.dashboard"))
    db.session.commit()
    # Переход на страницу проверки Stripe: документы вводятся там, не у нас
    return redirect(url, code=303)


@bp.route("/verify/return")
@login_required
def verify_return():
    flash(
        _("We are checking your details. This can take a few minutes. "
          "The result appears here and we will not send it by e-mail."),
        "info",
    )
    return redirect(url_for("member.dashboard"))


@bp.route("/confirm-email/resend", methods=["POST"])
@login_required
@limiter.limit("3 per hour")
def resend_confirmation():
    if current_user.email_confirmed_at is None:
        send_confirmation(current_user)
    flash(_("If your address is not yet confirmed, we have sent a new link."), "info")
    return redirect(url_for("member.dashboard"))


@bp.route("/privacy/export")
@login_required
@limiter.limit("5 per hour")
def export():
    audit("privacy.export", target=f"user:{current_user.id}", actor_id=current_user.id)
    db.session.commit()
    payload = json.dumps(export_user(current_user), ensure_ascii=False, indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="club-export.json"'},
    )


@bp.route("/privacy/delete", methods=["POST"])
@login_required
def delete_account():
    if not current_user.check_password(request.form.get("password", "")):
        flash(_("The password is not correct. Your account was not deleted."), "error")
        return redirect(url_for("member.dashboard"))

    try:
        delete_user(current_user, current_app.extensions["identity_provider"])
    except Exception:
        # Не удаляем у себя, если у провайдера не получилось: иначе данные останутся без контроля
        db.session.rollback()
        flash(_("Your account could not be deleted right now. Please try again later."), "error")
        return redirect(url_for("member.dashboard"))

    logout_user()
    flash(_("Your account and your data have been deleted."), "info")
    return redirect(url_for("public.index"))
