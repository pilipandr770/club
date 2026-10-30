"""Приглашения в клуб. Создаёт админ (поручитель), использует новый участник при регистрации."""

import secrets
from datetime import timedelta

from config.plans import PLANS

from ..extensions import db
from ..models.invites import Invite
from ..models.user import User
from ..utils import utcnow
from . import membership
from .audit import audit

MAX_USES = 50
VALID_DAYS = (7, 14, 30, 90)


class InviteError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def create(admin: User, note: str, email: str = "", plan: str = "guest", months: int = 0, max_uses: int = 1,
           days: int = 30) -> Invite:
    note = " ".join((note or "").split())
    if len(note) < membership.NOTE_MIN:
        raise InviteError("note_required")
    email = (email or "").strip().lower()
    if email and ("@" not in email or len(email) > 255):
        raise InviteError("email")
    if plan != "guest" and (plan not in PLANS or months not in membership.MONTH_CHOICES):
        raise InviteError("plan")
    if plan == "guest":
        months = 0
    if not 1 <= max_uses <= MAX_USES or days not in VALID_DAYS:
        raise InviteError("limits")
    invite = Invite(code=secrets.token_urlsafe(16), created_by_id=admin.id, note=note[:membership.NOTE_MAX],
                    email=email or None, plan=plan, months=months, max_uses=max_uses,
                    expires_at=utcnow() + timedelta(days=days))
    db.session.add(invite)
    db.session.flush()
    audit("admin.invite_created", target=f"invite:{invite.id}", actor_id=admin.id,
          details=f"{plan} {months} Mon., {max_uses}x, {days} Tage: {invite.note}")
    return invite


def state(invite: Invite, now=None) -> str:
    """active | used | expired | revoked"""
    now = now or utcnow()
    if invite.revoked_at:
        return "revoked"
    if invite.uses >= invite.max_uses:
        return "used"
    if invite.expires_at <= now:
        return "expired"
    return "active"


def find(code: str, lock: bool = False) -> Invite | None:
    """Действующее приглашение по коду или None. С lock=True строка блокируется до конца транзакции."""
    if not code or len(code) > 40:
        return None
    query = Invite.query.filter_by(code=code)
    invite = (query.with_for_update() if lock else query).first()
    return invite if invite is not None and state(invite) == "active" else None


def revoke(admin: User, invite: Invite) -> None:
    if invite.revoked_at is None:
        invite.revoked_at = utcnow()
        audit("admin.invite_revoked", target=f"invite:{invite.id}", actor_id=admin.id)


def redeem(invite: Invite, user: User) -> None:
    """Новый участник пришёл по приглашению: личность подтверждает поручитель, тариф выдаётся, если задан."""
    voucher = db.session.get(User, invite.created_by_id) if invite.created_by_id else None
    note = f"Einladung: {invite.note}"
    invite.uses += 1
    user.invite_id = invite.id
    if voucher is not None and voucher.id != user.id:
        membership.verify_manually(voucher, user, note)
        if invite.plan != "guest" and invite.months:
            membership.grant_plan(voucher, user, invite.plan, invite.months, "Einladung")
    audit("invite.redeemed", target=f"invite:{invite.id}", actor_id=user.id)
