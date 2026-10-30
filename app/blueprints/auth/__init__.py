import re
import time

from flask import (Blueprint, current_app, flash, redirect, render_template, request, session, url_for)
from flask_babel import force_locale, get_locale, gettext as _
from flask_login import current_user, login_required, login_user, logout_user

from ..._compat import safe_next
from ...countries import country_choices, is_known_country
from ...extensions import db, limiter
from ...models import Profile, User
from ...services.audit import audit
from ...services import invites, twofactor
from ...services.consents import record_consent
from ...services.mailer import send_mail
from ...services.tokens import make_email_token, read_email_token
from ...utils import utcnow

bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 10


def send_confirmation(user) -> None:
    link = f"{current_app.config['BASE_URL']}{url_for('auth.confirm', token=make_email_token(user))}"
    with force_locale(user.locale or current_app.config["DEFAULT_LOCALE"]):
        send_mail(
            user.email,
            _("Please confirm your e-mail address"),
            _("Please open this link to confirm your e-mail address:") + "\n\n" + link + "\n",
        )


def _blocked_residence(code: str) -> bool:
    return code in current_app.config["BLOCKED_RESIDENCE_COUNTRIES"]


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("member.dashboard"))

    errors: dict[str, str] = {}
    form = request.form
    invite = None
    if request.method == "POST":
        invite_code = form.get("invite", "").strip()
        invite = invites.find(invite_code, lock=True) if invite_code else None
        if invite_code and invite is None:
            errors["invite"] = _("This invitation is no longer valid. You can still register without it.")
        email = form.get("email", "").strip().lower()
        first_name = form.get("first_name", "").strip()
        last_name = form.get("last_name", "").strip()
        password = form.get("password", "")
        residence = form.get("residence_country", "").strip().upper()

        if not first_name:
            errors["first_name"] = _("Please enter your first name.")
        if not EMAIL_RE.match(email):
            errors["email"] = _("Please enter a valid e-mail address.")
        elif User.query.filter_by(email=email).first():
            errors["email"] = _("An account with this e-mail address already exists.")
        elif invite is not None and invite.email and invite.email != email:
            errors["email"] = _("This invitation was issued for a different e-mail address.")
        if len(password) < MIN_PASSWORD_LENGTH:
            errors["password"] = _("The password must be at least 10 characters long.")
        if not is_known_country(residence):
            errors["residence_country"] = _("Please choose your country of residence.")
        elif _blocked_residence(residence):
            errors["residence_country"] = _("We cannot open an account for residents of this country.")
        # По приглашению личность подтверждает поручитель, Stripe не нужен, поэтому и согласие на биометрию не требуется
        for consent in ("privacy", "terms") if invite is not None else ("privacy", "terms", "identity"):
            if not form.get(f"consent_{consent}"):
                errors[f"consent_{consent}"] = _("This confirmation is required to create an account.")

        if not errors:
            user = User(email=email, first_name=first_name[:80], last_name=last_name[:80], locale=str(get_locale()))
            user.set_password(password)
            user.profile = Profile(residence_country=residence)
            db.session.add(user)
            record_consent(user, "privacy", True)
            record_consent(user, "terms", True)
            if invite is None:
                record_consent(user, "identity", True)
            db.session.flush()
            audit("user.registered", target=f"user:{user.id}", actor_id=None)
            if invite is not None:
                invites.redeem(invite, user)
            db.session.commit()
            from ...services import analytics

            analytics.track_signup(request)
            send_confirmation(user)
            login_user(user)
            flash(_("Welcome. Please confirm your e-mail address to continue."), "success")
            return redirect(url_for("member.dashboard"))

    return render_template("auth/register.html", errors=errors, form=form, invite=invite,
                           countries=country_choices())


@bp.route("/join/<code>")
def join(code):
    """Ссылка-приглашение: та же регистрация, но личность уже подтверждена поручителем."""
    if current_user.is_authenticated:
        return redirect(url_for("member.dashboard"))
    invite = invites.find(code)
    if invite is None:
        flash(_("This invitation is no longer valid. You can still register without it."), "info")
        return redirect(url_for("auth.register"))
    return render_template("auth/register.html", errors={}, invite=invite, countries=country_choices(),
                           form={"invite": code, "email": invite.email or ""})


def _login_email_key():
    return "login:" + request.form.get("email", "").strip().lower()[:254]


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per minute; 40 per hour", methods=["POST"])
@limiter.limit("10 per hour", methods=["POST"], key_func=_login_email_key)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("member.dashboard"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(request.form.get("password", "")):
            error = _("E-mail or password is not correct.")
        elif user.status != "active":
            error = _("This account is blocked. Please contact support.")
        elif user.has_two_factor:
            # Пароль верен, но входом это ещё не считается: ждём код из приложения (или резервный)
            session["2fa_pending"] = {"uid": user.id, "remember": bool(request.form.get("remember")),
                                      "next": safe_next(request.args.get("next")), "at": time.time(), "fails": 0}
            return redirect(url_for("auth.two_factor"))
        else:
            _finish_login(user, bool(request.form.get("remember")))
            return redirect(safe_next(request.args.get("next")) or url_for("member.dashboard"))
    return render_template("auth/login.html", error=error)


def _finish_login(user, remember: bool) -> None:
    login_user(user, remember=remember)
    user.last_login_at = utcnow()
    db.session.commit()


TWO_FACTOR_WINDOW = 5 * 60
TWO_FACTOR_MAX_FAILS = 5


def _two_factor_key():
    return "2fa:" + str((session.get("2fa_pending") or {}).get("uid", "none"))


@bp.route("/login/2fa", methods=["GET", "POST"])
@limiter.limit("6 per minute; 30 per hour", methods=["POST"])
@limiter.limit("10 per hour", methods=["POST"], key_func=_two_factor_key)
def two_factor():
    """Второй шаг входа. Без верного пароля на первом шаге сюда не попасть, ждать код можно 5 минут и 5 попыток."""
    pending = session.get("2fa_pending")
    if not pending or time.time() - pending.get("at", 0) > TWO_FACTOR_WINDOW:
        session.pop("2fa_pending", None)
        flash(_("Please log in again."), "info")
        return redirect(url_for("auth.login"))
    user = db.session.get(User, pending["uid"])
    if user is None or user.status != "active" or not user.has_two_factor:
        session.pop("2fa_pending", None)
        return redirect(url_for("auth.login"))

    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        if twofactor.verify(user, code) or twofactor.use_recovery_code(user, code):
            session.pop("2fa_pending", None)
            _finish_login(user, pending.get("remember", False))
            return redirect(pending.get("next") or url_for("member.dashboard"))
        db.session.rollback()
        pending["fails"] = pending.get("fails", 0) + 1
        if pending["fails"] >= TWO_FACTOR_MAX_FAILS:
            session.pop("2fa_pending", None)
            flash(_("Too many wrong codes. Please log in again."), "error")
            return redirect(url_for("auth.login"))
        session["2fa_pending"] = pending
        error = _("The code is not correct. Please try again.")
    return render_template("auth/two_factor.html", error=error)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.pop("2fa_pending", None)
    logout_user()
    flash(_("You have been logged out."), "info")
    return redirect(url_for("public.index"))


@bp.route("/confirm/<token>")
def confirm(token):
    data = read_email_token(token)
    if data is None:
        flash(_("The link is invalid or has expired. Please request a new one."), "error")
        return redirect(url_for("member.dashboard") if current_user.is_authenticated else url_for("auth.login"))

    user = db.session.get(User, data["uid"])
    if user is None or user.email != data["email"]:
        flash(_("The link is invalid or has expired. Please request a new one."), "error")
        return redirect(url_for("auth.login"))

    if user.email_confirmed_at is None:
        user.email_confirmed_at = utcnow()
        audit("user.email_confirmed", target=f"user:{user.id}", actor_id=user.id)
        db.session.commit()
    flash(_("Your e-mail address is confirmed."), "success")
    if current_user.is_authenticated:
        return redirect(url_for("member.dashboard"))
    return redirect(url_for("auth.login"))
