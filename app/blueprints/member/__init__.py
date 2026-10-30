import json
import logging

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required, logout_user

from ...extensions import db, limiter
from ...modules import enabled_modules
from ...services import billing
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
    from ...profile_guide import completeness, matchable, missing_for_matching
    from ...services.entitlements import has_feature
    from config.plans import PLANS, DEFAULT_PLAN

    modules = [
        {"spec": spec, "available": has_feature(current_user, spec.feature)}
        for spec in enabled_modules(current_app.config["ENABLED_MODULES"])
    ]
    plan = PLANS.get(current_user.effective_plan, PLANS[DEFAULT_PLAN])
    return render_template(
        "member/dashboard.html",
        reason=reason_message(current_user.verification_reason),
        modules=modules,
        plan_name=plan["name"],
        publication_decided=latest_consent(current_user, "directory") is not None,
        profile_enabled=any(item["spec"].key == "profile" and item["available"] for item in modules),
        profile_percent=completeness(current_user.profile) if current_user.profile else 0,
        matching_ready=bool(current_user.profile and matchable(current_user.profile)),
        matching_fields_ready=bool(current_user.profile and not missing_for_matching(current_user.profile)),
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


# --------------------------------------------------------------------------- тариф и оплата
def _plan_cards():
    """Тарифы для страницы: описание, цены и то, что добавляют лимиты. Строки целиком, без склейки."""
    from config.plans import ASSISTANT_DAILY, PAID_PLANS, PLANS, TOOL_LIMITS

    descriptions = {
        "starter": _("Everything in Guest, plus partner services and website tools, law search and market panel, "
                     "booking and creating trips, creating and joining meetings, and mini-clubs."),
        "growth": _("Everything in Starter, plus publishing your startup for investors and offers for operators and agents."),
        "premium": _("Everything in Growth, plus the auction."),
    }
    cards = []
    for key in PAID_PLANS:
        data = PLANS[key]
        cards.append({
            "key": key, "name": data["name"], "description": descriptions[key],
            "month": data["price_eur_cents"], "year": data["price_year_eur_cents"],
            "year_saving": data["price_eur_cents"] * 12 - data["price_year_eur_cents"],
            "assistant": ASSISTANT_DAILY[key], "laws": TOOL_LIMITS[key]["laws"],
        })
    return cards


def _billing_error(code):
    return {
        "bad_choice": _("Please choose a plan and a billing period."),
        "not_verified": _("Plans are available after your identity is verified."),
        "already_subscribed": _("You already have a subscription. You can change or cancel it under Manage subscription."),
        "no_customer": _("There is no subscription to manage yet."),
        "not_configured": _("Payment is not available right now. Please try again later."),
    }.get(code, _("Payment is not available right now. Please try again later."))


@bp.route("/plan")
@login_required
def plan():
    from config.plans import PLANS

    return render_template("member/plan.html", cards=_plan_cards(), plan_names={k: v["name"] for k, v in PLANS.items()},
                           state=billing.plan_state(current_user),
                           can_subscribe=billing.can_subscribe(current_user),
                           has_subscription=billing.has_subscription(current_user),
                           has_customer=bool(current_user.stripe_customer_id))


@bp.route("/plan/checkout", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def plan_checkout():
    try:
        url = billing.start_checkout(current_user, request.form.get("plan", ""), request.form.get("interval", ""))
    except billing.BillingError as exc:
        db.session.rollback()
        flash(_billing_error(exc.code), "error")
        return redirect(url_for("member.plan"))
    except Exception:
        db.session.rollback()
        log.exception("Checkout konnte nicht gestartet werden")
        flash(_billing_error("not_configured"), "error")
        return redirect(url_for("member.plan"))
    db.session.commit()
    return redirect(url, code=303)


@bp.route("/plan/portal", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def plan_portal():
    try:
        url = billing.portal_url(current_user)
    except billing.BillingError as exc:
        flash(_billing_error(exc.code), "error")
        return redirect(url_for("member.plan"))
    except Exception:
        log.exception("Kundenportal konnte nicht geöffnet werden")
        flash(_billing_error("not_configured"), "error")
        return redirect(url_for("member.plan"))
    return redirect(url, code=303)


@bp.route("/plan/return")
@login_required
def plan_return():
    if request.args.get("status") == "ok":
        flash(_("Thank you. Your payment is being confirmed. Your plan appears here within a minute."), "success")
    return redirect(url_for("member.plan"))


# --------------------------------------------------------------------------- безопасность аккаунта: второй фактор
def _two_factor_error(code):
    return {
        "bad_code": _("The code is not correct. Please try again."),
        "bad_password": _("The password is not correct."),
        "admin_required": _("Two-factor login is required for administrators and cannot be switched off."),
    }.get(code, _("Something went wrong. Please try again."))


def _security_page(codes=None):
    from flask import session

    from ...services import twofactor

    secret = session.get("totp_setup") if not current_user.has_two_factor else None
    qr = twofactor.qr_svg(twofactor.provisioning_uri(current_user, secret)) if secret else None
    return render_template(
        "member/security.html", codes=codes, qr=qr, setup_secret=secret,
        codes_left=twofactor.recovery_codes_left(current_user) if current_user.has_two_factor else 0,
        required=current_user.is_admin and current_app.config.get("ADMIN_2FA_REQUIRED", True))


@bp.route("/security")
@login_required
def security():
    return _security_page()


@bp.route("/security/setup", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def security_setup():
    from flask import session

    from ...services import twofactor

    if not current_user.has_two_factor:
        session["totp_setup"] = twofactor.new_secret()
    return redirect(url_for("member.security"))


@bp.route("/security/confirm", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def security_confirm():
    from flask import session

    from ...services import twofactor

    secret = session.get("totp_setup")
    if not secret or current_user.has_two_factor:
        return redirect(url_for("member.security"))
    try:
        codes = twofactor.enable(current_user, secret, request.form.get("code", ""))
        db.session.commit()
    except twofactor.TwoFactorError as exc:
        db.session.rollback()
        flash(_two_factor_error(exc.code), "error")
        return _security_page()
    session.pop("totp_setup", None)
    flash(_("Two-factor login is on."), "success")
    return _security_page(codes=codes)


@bp.route("/security/disable", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def security_disable():
    from ...services import twofactor

    try:
        twofactor.disable(current_user, request.form.get("password", ""), request.form.get("code", ""))
        db.session.commit()
    except twofactor.TwoFactorError as exc:
        db.session.rollback()
        flash(_two_factor_error(exc.code), "error")
    else:
        flash(_("Two-factor login is off."), "success")
    return redirect(url_for("member.security"))


@bp.route("/security/codes", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def security_codes():
    from ...services import twofactor

    try:
        codes = twofactor.regenerate_codes(current_user, request.form.get("password", ""), request.form.get("code", ""))
        db.session.commit()
    except twofactor.TwoFactorError as exc:
        db.session.rollback()
        flash(_two_factor_error(exc.code), "error")
        return redirect(url_for("member.security"))
    return _security_page(codes=codes)
