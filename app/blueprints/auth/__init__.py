import re

from flask import (Blueprint, current_app, flash, redirect, render_template, request, url_for)
from flask_babel import force_locale, get_locale, gettext as _
from flask_login import current_user, login_required, login_user, logout_user

from ..._compat import safe_next
from ...countries import country_choices, is_known_country
from ...extensions import db, limiter
from ...models import Profile, User
from ...services.audit import audit
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
    if request.method == "POST":
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
        if len(password) < MIN_PASSWORD_LENGTH:
            errors["password"] = _("The password must be at least 10 characters long.")
        if not is_known_country(residence):
            errors["residence_country"] = _("Please choose your country of residence.")
        elif _blocked_residence(residence):
            errors["residence_country"] = _("We cannot open an account for residents of this country.")
        for consent in ("privacy", "terms", "identity"):
            if not form.get(f"consent_{consent}"):
                errors[f"consent_{consent}"] = _("This confirmation is required to create an account.")

        if not errors:
            user = User(email=email, first_name=first_name[:80], last_name=last_name[:80], locale=str(get_locale()))
            user.set_password(password)
            user.profile = Profile(residence_country=residence)
            db.session.add(user)
            record_consent(user, "privacy", True)
            record_consent(user, "terms", True)
            record_consent(user, "identity", True)
            audit("user.registered", target=f"user:{user.id}", actor_id=None)
            db.session.commit()
            from ...services import analytics

            analytics.track_signup(request)
            send_confirmation(user)
            login_user(user)
            flash(_("Welcome. Please confirm your e-mail address to continue."), "success")
            return redirect(url_for("member.dashboard"))

    return render_template("auth/register.html", errors=errors, form=form,
                           countries=country_choices())


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per minute; 40 per hour", methods=["POST"])
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
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            user.last_login_at = utcnow()
            db.session.commit()
            return redirect(safe_next(request.args.get("next")) or url_for("member.dashboard"))
    return render_template("auth/login.html", error=error)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
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
