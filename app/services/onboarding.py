"""Онбординг: одно напоминание участнику, который прошёл проверку, но не заполнил профиль.

Письмо уходит один раз (User.onboarding_nudged_at), через сутки после регистрации и не позже чем через 14 дней.
Участники, которые уже подходят для подбора или заполнили профиль на 60 % и больше, письма не получают.
"""

from datetime import timedelta

from flask import current_app, url_for
from flask_babel import force_locale, gettext as _

from ..extensions import db
from ..models.user import User
from ..profile_guide import completeness, matchable
from ..utils import utcnow
from .mailer import send_mail

AFTER = timedelta(hours=24)
WITHIN = timedelta(days=14)
ENOUGH = 60


def due(now=None):
    now = now or utcnow()
    candidates = (User.query.filter(User.status == "active", User.verification_status == "verified",
                                    User.email_confirmed_at.isnot(None), User.onboarding_nudged_at.is_(None),
                                    User.created_at <= now - AFTER, User.created_at >= now - WITHIN).all())
    return [user for user in candidates if user.profile is not None
            and not matchable(user.profile) and completeness(user.profile) < ENOUGH]


def send_nudge(user) -> bool:
    link = f"{current_app.config['BASE_URL'].rstrip('/')}{url_for('profile.edit')}"
    with force_locale(user.locale or current_app.config["DEFAULT_LOCALE"]):
        body = (_("Hello %(name)s,", name=user.first_name) + "\n\n"
                + _("Welcome to the club. Your profile is the base for good matches: one concrete need and one way you can help are enough to start.") + "\n\n"
                + _("Please complete your profile here:") + "\n" + link + "\n\n"
                + _("Matching and the member directory stay off until you choose them yourself.") + "\n")
        return send_mail(user.email, _("Two minutes for your profile"), body)


def run(now=None) -> int:
    """Отправляет напоминания. Отметка ставится, даже если почта не настроена: письмо не должно уходить пачкой позже."""
    sent = 0
    for user in due(now):
        send_nudge(user)
        user.onboarding_nudged_at = now or utcnow()
        db.session.commit()
        sent += 1
    return sent
