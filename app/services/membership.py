"""Ручное управление членством для админов: подтверждение личности и оплаченный срок тарифа.

Нужно для людей из тёплого круга, которых админ знает лично и оплату от которых принимает вручную.
Каждое действие обязательно пишется в журнал с пометкой, кто поручился и почему.
"""

from datetime import timedelta

from config.plans import PLANS

from ..extensions import db
from ..models.user import User
from ..utils import add_months, utcnow
from .audit import audit

NOTE_MIN = 5
NOTE_MAX = 300
MONTH_CHOICES = (1, 3, 6, 12)


class MembershipError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _note(value) -> str:
    note = " ".join((value or "").split())
    if len(note) < NOTE_MIN:
        raise MembershipError("note_required")
    return note[:NOTE_MAX]


def verify_manually(admin: User, user: User, note: str) -> None:
    """Личность подтверждена админом, который знает человека лично. Сам себя админ подтвердить не может."""
    if admin.id == user.id:
        raise MembershipError("self")
    note = _note(note)
    user.verification_status = "verified"
    user.verification_reason = "manual_override"
    user.verification_method = "manual"
    user.verified_by_id = admin.id
    user.verification_note = note
    user.verified_at = utcnow()
    audit("admin.verified_manually", target=f"user:{user.id}", actor_id=admin.id, details=note)


def revoke_verification(admin: User, user: User, note: str) -> None:
    """Снимает подтверждение (ошибка или человек больше не поручительство). Участник теряет доступ к функциям клуба."""
    if admin.id == user.id:
        raise MembershipError("self")
    note = _note(note)
    user.verification_status = "none"
    user.verification_reason = None
    user.verification_method = None
    user.verified_by_id = None
    user.verification_note = None
    user.verified_at = None
    audit("admin.verification_revoked", target=f"user:{user.id}", actor_id=admin.id, details=note)


def grant_plan(admin: User, user: User, plan: str, months: int, note: str, now=None):
    """Принята оплата вручную: тариф на N месяцев. Продлевает от конца текущего срока, если он ещё не истёк."""
    if plan not in PLANS or plan == "guest":
        raise MembershipError("plan")
    if months not in MONTH_CHOICES:
        raise MembershipError("months")
    note = _note(note)
    now = now or utcnow()
    start = user.paid_until if user.paid_until and user.paid_until > now and user.plan == plan else now
    user.plan = plan
    user.paid_until = add_months(start, months)
    user.payment_note = note
    audit("admin.plan_granted", target=f"user:{user.id}", actor_id=admin.id,
          details=f"{plan} +{months} Mon. bis {user.paid_until:%d.%m.%Y}: {note}")
    return user.paid_until


def end_plan(admin: User, user: User, note: str) -> None:
    """Тариф заканчивается сразу: участник возвращается на гостевой."""
    note = _note(note)
    user.plan = "guest"
    user.paid_until = None
    user.payment_note = note
    audit("admin.plan_ended", target=f"user:{user.id}", actor_id=admin.id, details=note)


def expiring(days: int = 14, now=None):
    """Участники, у которых оплаченный срок заканчивается в ближайшие дни."""
    now = now or utcnow()
    return (User.query.filter(User.plan != "guest", User.paid_until.isnot(None),
                              User.paid_until >= now, User.paid_until <= now + timedelta(days=days))
            .order_by(User.paid_until).all())


def expired(now=None):
    now = now or utcnow()
    return (User.query.filter(User.plan != "guest", User.paid_until.isnot(None), User.paid_until < now)
            .order_by(User.paid_until.desc()).limit(100).all())


def waiting_for_identity():
    return (User.query.filter(User.verification_status != "verified", User.status == "active")
            .order_by(User.created_at.desc()).limit(100).all())
