from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_babel import lazy_gettext as _l
from flask_login import current_user, login_required

from config.plans import PLANS
from ...extensions import db
from ...models import AuditLog, Consent, Lot, User
from ...models.user import ROLES, VERIFICATION_STATUSES
from ...services.audit import audit
from ...models import Event, MiniClub, MiniClubReport, Startup, Trip
from ...services import events as event_service
from ...services import miniclubs as miniclub_service
from ...services import travel as travel_service
from ...services.ai import status as ai_status
from ...services.ai import usage as ai_usage
from ...services import startups as startup_service
from ...services.auction_engine import (
    AuctionError,
    approve_lot,
    cancel_lot,
    create_lot,
    reject_lot,
    validate_lot_input,
)
from ...services import membership
from ...services.membership import MONTH_CHOICES, MembershipError
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
        if current_app.config.get("ADMIN_2FA_REQUIRED", True) and not current_user.has_two_factor:
            flash(_("Administrators need two-factor login. Please set it up first."), "info")
            return redirect(url_for("member.security"))
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
        ai=ai_status(),
        ai_summary=ai_usage.summary(),
        expiring=membership.expiring(14),
        expired_count=len(membership.expired()),
        waiting_count=len(membership.waiting_for_identity()),
    )


ANALYTICS_PERIODS = (7, 30, 90)


@bp.route("/ai")
@admin_required
def ai_usage_view():
    """Расход ИИ: токены сегодня и за месяц против лимитов, разбивка по функциям, график по дням."""
    from ...services.ai import usage

    days = request.args.get("days", 30, type=int)
    if days not in ANALYTICS_PERIODS:
        days = 30
    series = usage.daily(days)
    peak_real = max([tokens for _day, tokens in series] + [0])
    return render_template("admin/ai_usage.html", days=days, periods=ANALYTICS_PERIODS, summary=usage.summary(),
                           rows=usage.by_feature(days), series=series, peak=max(peak_real, 1), peak_real=peak_real,
                           ai=ai_status())


@bp.route("/analytics")
@admin_required
def analytics_view():
    """Анонимная статистика посещений публичных страниц: посетители, страны, города, источники, воронка."""
    from ...countries import country_choices
    from ...services import analytics as stats

    days = request.args.get("days", 30, type=int)
    if days not in ANALYTICS_PERIODS:
        days = 30
    names = dict(country_choices())
    totals = stats.totals(days)
    series = stats.daily(days)
    peak_real = max([count for _day, count in series] + [0])
    peak = max(peak_real, 1)
    visitors = totals["visitors"]
    cities = stats.city_rows(days)
    countries = [(code, names.get(code, code) if code else "", count) for code, count in stats.top("country", days)]
    located = sum(count for code, _name, count in countries if code)
    return render_template(
        "admin/analytics.html", days=days, periods=ANALYTICS_PERIODS, totals=totals, series=series, peak=peak, peak_real=peak_real,
        countries=countries, cities=cities, names=names, sources=stats.top("source", days),
        devices=stats.top("device", days), pages=stats.top("page", days, metric="views"),
        located_share=round(100 * located / visitors) if visitors else 0,
        rate=lambda part: round(100 * part / visitors, 1) if visitors else 0,
        header_check={
            "country": request.headers.get("CF-IPCountry", ""),
            "city": request.headers.get("CF-IPCity", ""),
            "cf_ip": bool(request.headers.get("CF-Connecting-IP")),
            "forwarded": request.headers.get("X-Forwarded-For", ""),
            "remote": request.remote_addr or "",
        })


@bp.route("/users")
@admin_required
def users():
    query = request.args.get("q", "").strip().lower()
    mode = request.args.get("show", "")
    items = User.query
    if query:
        items = items.filter(User.email.ilike(f"%{query}%"))
    if mode == "waiting":
        items = items.filter(User.verification_status != "verified", User.status == "active")
    elif mode == "expiring":
        ids = [u.id for u in membership.expiring(14)]
        items = items.filter(User.id.in_(ids or [0]))
    elif mode == "expired":
        ids = [u.id for u in membership.expired()]
        items = items.filter(User.id.in_(ids or [0]))
    elif mode == "manual":
        items = items.filter(User.verification_method == "manual")
    items = items.order_by(User.created_at.desc()).limit(200).all()
    return render_template("admin/users.html", users=items, q=query, plans=PLANS, mode=mode)


def _change_list(user, form):
    """Применяет изменения формы к участнику и возвращает список того, что изменилось."""
    changes = []

    plan = form.get("plan", user.plan)
    if plan in PLANS and plan != user.plan:
        changes.append(f"plan {user.plan}->{plan}")
        user.plan = plan
        if plan == "guest":
            user.paid_until = None

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
    if vstatus == "verified" and user.verification_status != "verified":
        vstatus = user.verification_status  # подтверждение только через форму с пометкой (verify_manually)
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
                           reasons=VERIFICATION_REASONS, history=history, consents=consents,
                           month_choices=MONTH_CHOICES,
                           verified_by=db.session.get(User, user.verified_by_id) if user.verified_by_id else None)


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


@bp.route("/lots/<int:lot_id>/review", methods=["POST"])
@admin_required
def review_lot(lot_id):
    lot = db.session.get(Lot, lot_id) or abort(404)
    try:
        if request.form.get("decision") == "approve":
            approve_lot(current_user, lot)
            flash(_("The lot is published."), "success")
        else:
            reject_lot(current_user, lot, request.form.get("reason", ""))
            flash(_("The lot is rejected."), "success")
        db.session.commit()
    except AuctionError:
        db.session.rollback()
        flash(_("This lot is not waiting for review."), "error")
    return redirect(url_for("admin.lots"))


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


@bp.route("/events")
@admin_required
def events():
    status = request.args.get("status", "pending")
    query = Event.query
    if status in ("pending", "open", "rejected", "cancelled"):
        query = query.filter_by(status=status)
    items = query.order_by(Event.starts_at).limit(200).all()
    return render_template("admin/events.html", items=items, status=status)


@bp.route("/events/<int:event_id>/review", methods=["POST"])
@admin_required
def review_event(event_id):
    event = db.session.get(Event, event_id) or abort(404)
    try:
        if request.form.get("decision") == "approve":
            event_service.approve_event(current_user, event)
            flash(_("The meeting is published."), "success")
        else:
            event_service.reject_event(current_user, event, request.form.get("reason", ""))
            flash(_("The meeting is rejected."), "success")
        db.session.commit()
    except event_service.EventError:
        db.session.rollback()
        flash(_("This meeting is not waiting for review."), "error")
    return redirect(url_for("admin.events", status="pending"))


@bp.route("/miniclubs")
@admin_required
def miniclubs():
    from ...miniclub_guide import flagged_terms

    status = request.args.get("status", "pending")
    query = MiniClub.query
    if status in ("pending", "open", "rejected", "closed"):
        query = query.filter_by(status=status)
    items = query.order_by(MiniClub.created_at.desc()).limit(200).all()
    flags = {c.id: flagged_terms(c.name, c.description, c.audience, c.schedule, c.rules) for c in items}
    reports = (MiniClubReport.query.filter_by(handled=False).order_by(MiniClubReport.created_at).all())
    return render_template("admin/miniclubs.html", items=items, status=status, flags=flags, reports=reports)


@bp.route("/miniclubs/<int:club_id>/review", methods=["POST"])
@admin_required
def review_miniclub(club_id):
    club = db.session.get(MiniClub, club_id) or abort(404)
    decision = request.form.get("decision")
    try:
        if decision == "approve":
            miniclub_service.approve_club(current_user, club)
            flash(_("The mini-club is published."), "success")
        elif decision == "close":
            miniclub_service.close_club(current_user, club, request.form.get("reason", ""))
            flash(_("The mini-club is closed."), "success")
        else:
            miniclub_service.reject_club(current_user, club, request.form.get("reason", ""))
            flash(_("The mini-club is rejected."), "success")
        db.session.commit()
    except miniclub_service.MiniClubError:
        db.session.rollback()
        flash(_("This mini-club is not waiting for review."), "error")
    return redirect(url_for("admin.miniclubs", status="pending"))


@bp.route("/miniclubs/reports/<int:report_id>/handled", methods=["POST"])
@admin_required
def handle_miniclub_report(report_id):
    report = db.session.get(MiniClubReport, report_id) or abort(404)
    miniclub_service.handle_report(current_user, report)
    db.session.commit()
    return redirect(url_for("admin.miniclubs", status="open"))


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


@bp.route("/travel")
@admin_required
def travel():
    status = request.args.get("status", "pending")
    query = Trip.query.filter_by(kind="offer")
    if status in ("pending", "open", "rejected"):
        query = query.filter_by(status=status)
    items = query.order_by(Trip.created_at.desc()).limit(200).all()
    return render_template("admin/travel.html", items=items, status=status)


@bp.route("/travel/<int:trip_id>/review", methods=["POST"])
@admin_required
def review_travel(trip_id):
    trip = db.session.get(Trip, trip_id) or abort(404)
    if trip.kind != "offer":
        abort(404)
    if request.form.get("decision") == "approve":
        travel_service.approve_offer(current_user, trip)
        flash(_("The offer is published."), "success")
    elif request.form.get("decision") == "reject":
        travel_service.reject_offer(current_user, trip, request.form.get("reason", ""))
        flash(_("The offer is rejected."), "success")
    else:
        flash(_("Nothing has changed."), "info")
        return redirect(url_for("admin.travel"))
    db.session.commit()
    return redirect(url_for("admin.travel", status="pending"))


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    """Создание участника вручную: без регистрации и без Stripe. Для демонстраций и приглашений."""
    import secrets as _secrets

    from ...countries import country_choices, is_known_country
    from ...models import Profile
    from ...services.consents import record_consent  # noqa: F401  (согласия админ не записывает за участника)

    errors = []
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        first_name = request.form.get("first_name", "").strip()
        residence = request.form.get("residence_country", "").strip().upper()
        plan = request.form.get("plan", "guest")
        password = request.form.get("password", "")
        if "@" not in email or User.query.filter_by(email=email).first() is not None:
            errors.append("email")
        if not first_name:
            errors.append("first_name")
        if not is_known_country(residence):
            errors.append("residence_country")
        if plan not in PLANS:
            errors.append("plan")
        note = request.form.get("verification_note", "")
        if request.form.get("verified") and len(" ".join(note.split())) < membership.NOTE_MIN:
            errors.append("verification_note")
        months = request.form.get("months", type=int)
        if plan != "guest" and months:
            if months not in MONTH_CHOICES:
                errors.append("months")
            elif len(" ".join(request.form.get("payment_note", "").split())) < membership.NOTE_MIN:
                errors.append("payment_note")
        generated = not password
        if generated:
            password = _secrets.token_urlsafe(10)
        elif len(password) < 10:
            errors.append("password")
        if not errors:
            user = User(email=email, first_name=first_name[:80], last_name=request.form.get("last_name", "").strip()[:80],
                        plan=plan, is_admin=bool(request.form.get("is_admin")), email_confirmed_at=utcnow())
            user.set_password(password)
            user.profile = Profile(residence_country=residence)
            db.session.add(user)
            db.session.flush()
            audit("admin.user_created", target=f"user:{user.id}",
                  details=f"plan={plan} verified={bool(request.form.get('verified'))} admin={user.is_admin}")
            if request.form.get("verified"):
                membership.verify_manually(current_user, user, note)
            if plan != "guest" and months:
                membership.grant_plan(current_user, user, plan, months, request.form.get("payment_note", ""))
            db.session.commit()
            if generated:
                flash(_("Participant created. Temporary password (shown once): %(password)s", password=password), "success")
            else:
                flash(_("Participant created."), "success")
            return redirect(url_for("admin.user_detail", user_id=user.id))
        flash(_("Please check these fields: %(fields)s", fields=", ".join(errors)), "error")

    return render_template("admin/user_new.html", plans=PLANS, countries=country_choices(), errors=errors,
                           form=request.form, month_choices=MONTH_CHOICES)


def _membership_error(exc):
    return {
        "note_required": _("Please write a short note: how you know this person or how the payment arrived."),
        "self": _("You cannot do this for your own account. Ask another administrator."),
        "plan": _("Please choose a paid plan."),
        "months": _("Please choose the number of months."),
    }.get(exc.code, _("Something went wrong. Please try again."))


def _run_membership(user_id, action, success):
    user = db.session.get(User, user_id) or abort(404)
    try:
        action(user)
        db.session.commit()
        flash(success, "success")
    except MembershipError as exc:
        db.session.rollback()
        flash(_membership_error(exc), "error")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.route("/users/<int:user_id>/verify-manually", methods=["POST"])
@admin_required
def verify_manually(user_id):
    """Подтверждение личности вручную, в обход Stripe: админ знает человека лично. Пометка обязательна."""
    note = request.form.get("note", "")
    return _run_membership(user_id, lambda u: membership.verify_manually(current_user, u, note),
                           _("The identity check is marked as verified."))


@bp.route("/users/<int:user_id>/revoke-verification", methods=["POST"])
@admin_required
def revoke_verification(user_id):
    note = request.form.get("note", "")
    return _run_membership(user_id, lambda u: membership.revoke_verification(current_user, u, note),
                           _("The identity confirmation is withdrawn."))


@bp.route("/users/<int:user_id>/grant-plan", methods=["POST"])
@admin_required
def grant_plan(user_id):
    """Оплата принята вручную: тариф на выбранное число месяцев."""
    plan = request.form.get("plan", "")
    months = request.form.get("months", type=int) or 0
    note = request.form.get("note", "")
    return _run_membership(user_id, lambda u: membership.grant_plan(current_user, u, plan, months, note),
                           _("The plan is activated."))


@bp.route("/users/<int:user_id>/reset-2fa", methods=["POST"])
@admin_required
def reset_two_factor(user_id):
    """Сброс второго фактора у человека, потерявшего телефон и резервные коды. Нужна пометка, себя сбросить нельзя."""
    from ...services import twofactor

    user = db.session.get(User, user_id) or abort(404)
    try:
        twofactor.admin_reset(current_user, user, request.form.get("note", ""))
        db.session.commit()
        flash(_("Two-factor login is reset. The member sets it up again."), "success")
    except twofactor.TwoFactorError as exc:
        db.session.rollback()
        messages = {"self": _("You cannot reset your own two-factor login."),
                    "note_required": _("Please add a short note (at least 5 characters)."),
                    "not_enabled": _("Two-factor login is not on for this member.")}
        flash(messages.get(exc.code, _("Something went wrong. Please try again.")), "error")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.route("/users/<int:user_id>/end-plan", methods=["POST"])
@admin_required
def end_plan(user_id):
    note = request.form.get("note", "")
    return _run_membership(user_id, lambda u: membership.end_plan(current_user, u, note),
                           _("The plan is ended. The member is back on Guest."))


INVITE_ERRORS = {
    "note_required": _l("Please write who this person is and how you know them (at least 5 characters)."),
    "email": _l("Please enter a valid e-mail address or leave the field empty."),
    "plan": _l("Please choose a plan together with the paid period."),
    "limits": _l("Please choose the number of uses and the validity from the lists."),
}


@bp.route("/invites", methods=["GET", "POST"])
@admin_required
def invites_view():
    """Приглашения: создать ссылку, ручаясь за человека, и видеть, кто по ней пришёл."""
    from ...models import Invite
    from ...services import invites as invite_service

    if request.method == "POST":
        try:
            invite_service.create(
                current_user, request.form.get("note", ""), request.form.get("email", ""),
                request.form.get("plan", "guest"), request.form.get("months", 0, type=int),
                request.form.get("max_uses", 1, type=int), request.form.get("days", 30, type=int))
        except invite_service.InviteError as exc:
            flash(str(INVITE_ERRORS.get(exc.code, _l("The invitation could not be created."))), "error")
        else:
            db.session.commit()
            flash(_("The invitation is ready. Copy the link below and send it to the person."), "success")
        return redirect(url_for("admin.invites_view"))
    rows = Invite.query.order_by(Invite.created_at.desc()).limit(100).all()
    joined = {}
    for member in User.query.filter(User.invite_id.in_([row.id for row in rows] or [0])).all():
        joined.setdefault(member.invite_id, []).append(member)
    base = current_app.config["BASE_URL"].rstrip("/")
    return render_template(
        "admin/invites.html", plans=PLANS, month_choices=MONTH_CHOICES, valid_days=invite_service.VALID_DAYS,
        max_uses=invite_service.MAX_USES, state=invite_service.state, joined=joined,
        rows=[(row, f"{base}{url_for('auth.join', code=row.code)}") for row in rows])


@bp.route("/invites/<int:invite_id>/revoke", methods=["POST"])
@admin_required
def revoke_invite(invite_id):
    from ...models import Invite
    from ...services import invites as invite_service

    invite = db.get_or_404(Invite, invite_id)
    invite_service.revoke(current_user, invite)
    db.session.commit()
    flash(_("The invitation is revoked. Its link no longer works."), "success")
    return redirect(url_for("admin.invites_view"))


SERVICE_FIELDS = ("title", "summary", "description", "benefits", "member_benefit", "price_hint",
                  "provider_name", "provider_email")


def _service_data(form):
    data = {key: form.get(key, "").strip() for key in SERVICE_FIELDS}
    data["active"] = bool(form.get("active"))
    try:
        data["sort"] = int(form.get("sort", "0") or 0)
    except ValueError:
        data["sort"] = 0
    return data


@bp.route("/services")
@admin_required
def services_admin():
    from ...models import Service

    items = Service.query.order_by(Service.sort, Service.title).all()
    return render_template("admin/services.html", items=items)


@bp.route("/services/new", methods=["GET", "POST"])
@bp.route("/services/<int:service_id>/edit", methods=["GET", "POST"])
@admin_required
def service_edit(service_id=None):
    from ...models import Service
    from ...services.catalog import save_service

    service = db.session.get(Service, service_id) if service_id else None
    if service_id and service is None:
        abort(404)
    if request.method == "POST":
        data = _service_data(request.form)
        if not data["title"]:
            flash(_("Please enter a title."), "error")
        else:
            save_service(current_user, service, data)
            db.session.commit()
            flash(_("The service is saved."), "success")
            return redirect(url_for("admin.services_admin"))
    return render_template("admin/service_form.html", service=service,
                           form=request.form if request.method == "POST" else None)


@bp.route("/services/inquiries")
@admin_required
def service_inquiries():
    from ...models import ServiceInquiry

    items = ServiceInquiry.query.order_by(ServiceInquiry.created_at.desc()).limit(300).all()
    return render_template("admin/service_inquiries.html", items=items)


@bp.route("/services/inquiries/<int:inquiry_id>/status", methods=["POST"])
@admin_required
def service_inquiry_status(inquiry_id):
    from ...models import ServiceInquiry
    from ...services.catalog import CatalogError, set_inquiry_status

    inquiry = db.session.get(ServiceInquiry, inquiry_id) or abort(404)
    try:
        set_inquiry_status(current_user, inquiry, request.form.get("status", ""))
        db.session.commit()
    except CatalogError:
        db.session.rollback()
        flash(_("Unknown status."), "error")
    return redirect(url_for("admin.service_inquiries"))
