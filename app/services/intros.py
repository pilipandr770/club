"""Запросы на контакт. Контакт (e-mail) виден только после принятого запроса (CLAUDE.md, раздел «Данные и приватность»).

Правила:
- обе стороны — проверенные участники с согласием на матчинг и заполненным профилем;
- одна открытая или принятая связь на пару, в любом направлении;
- не больше INTRO_DAILY_LIMIT запросов в сутки на отправителя;
- ответить может только получатель, отозвать — только отправитель;
- принять нельзя, если кто-то уже отозвал согласие на матчинг.
"""

from datetime import timedelta

from sqlalchemy import or_

from ..extensions import db
from ..models import IntroRequest, User
from ..profile_guide import matchable
from ..utils import utcnow
from .audit import audit

INTRO_DAILY_LIMIT = 10
MESSAGE_MAX = 500


class IntroError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _can_match(user) -> bool:
    return (user is not None and user.status == "active" and user.is_verified
            and user.profile is not None and matchable(user.profile))


def _open_between(a_id: int, b_id: int):
    return (IntroRequest.query
            .filter(IntroRequest.status.in_(("pending", "accepted")))
            .filter(or_(((IntroRequest.from_user_id == a_id) & (IntroRequest.to_user_id == b_id)),
                        ((IntroRequest.from_user_id == b_id) & (IntroRequest.to_user_id == a_id))))
            .first())


def request_intro(sender: User, recipient: User, message: str, now=None) -> IntroRequest:
    now = now or utcnow()
    if sender.id == recipient.id:
        raise IntroError("own_profile")
    if not _can_match(sender) or not _can_match(recipient):
        raise IntroError("not_available")
    if _open_between(sender.id, recipient.id) is not None:
        raise IntroError("already_open")
    recent = IntroRequest.query.filter(IntroRequest.from_user_id == sender.id,
                                       IntroRequest.created_at >= now - timedelta(hours=24)).count()
    if recent >= INTRO_DAILY_LIMIT:
        raise IntroError("daily_limit")

    intro = IntroRequest(from_user_id=sender.id, to_user_id=recipient.id,
                         message=message.strip()[:MESSAGE_MAX], status="pending", created_at=now)
    db.session.add(intro)
    db.session.flush()
    audit("intro.requested", target=f"user:{recipient.id}", actor_id=sender.id)
    return intro


def respond(recipient: User, intro: IntroRequest, accept: bool) -> None:
    if intro.to_user_id != recipient.id:
        raise IntroError("forbidden")
    if intro.status != "pending":
        raise IntroError("already_answered")
    if accept:
        sender = db.session.get(User, intro.from_user_id)
        if not _can_match(sender) or not _can_match(recipient):
            raise IntroError("not_available")
    intro.status = "accepted" if accept else "declined"
    intro.responded_at = utcnow()
    audit("intro.answered", target=f"intro:{intro.id}", details=intro.status, actor_id=recipient.id)


def withdraw(sender: User, intro: IntroRequest) -> None:
    if intro.from_user_id != sender.id:
        raise IntroError("forbidden")
    if intro.status != "pending":
        raise IntroError("already_answered")
    intro.status = "withdrawn"
    intro.responded_at = utcnow()
    audit("intro.withdrawn", target=f"intro:{intro.id}", actor_id=sender.id)


def visible_contact(viewer, other: User):
    """E-mail другого участника, если между ними есть принятый запрос. Иначе None."""
    if viewer is None or other is None or viewer.id == other.id:
        return None
    accepted = (IntroRequest.query
                .filter(IntroRequest.status == "accepted")
                .filter(or_(((IntroRequest.from_user_id == viewer.id) & (IntroRequest.to_user_id == other.id)),
                            ((IntroRequest.from_user_id == other.id) & (IntroRequest.to_user_id == viewer.id))))
                .first())
    return other.email if accepted else None


def inbox(user: User):
    incoming = (IntroRequest.query.filter_by(to_user_id=user.id)
                .order_by(IntroRequest.created_at.desc()).limit(100).all())
    outgoing = (IntroRequest.query.filter_by(from_user_id=user.id)
                .order_by(IntroRequest.created_at.desc()).limit(100).all())
    return incoming, outgoing


def status_label(status: str) -> str:
    from flask_babel import gettext as _

    return {
        "declined": _("Not accepted this time."),
        "withdrawn": _("Withdrawn."),
        "accepted": _("Accepted."),
        "pending": _("Waiting for an answer."),
    }.get(status, status)


def link_state(viewer: User, other: User) -> str | None:
    """Состояние связи для показа: accepted, sent (мой запрос ждёт ответа), received (мне ждут ответа) или None."""
    intro = _open_between(viewer.id, other.id)
    if intro is None:
        return None
    if intro.status == "accepted":
        return "accepted"
    return "sent" if intro.from_user_id == viewer.id else "received"


def already_linked_ids(user: User) -> set:
    """Участники, с которыми уже есть открытый или принятый запрос."""
    rows = IntroRequest.query.filter(IntroRequest.status.in_(("pending", "accepted")),
                                     or_(IntroRequest.from_user_id == user.id,
                                         IntroRequest.to_user_id == user.id)).all()
    return {r.to_user_id if r.from_user_id == user.id else r.from_user_id for r in rows}

