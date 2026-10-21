"""Мини-клубы: создание, проверка, заявки, объявления, жалобы.

Клуб только знакомит участников: помещений и платежей нет. Организатор отвечает за правила группы,
общие правила (miniclub_guide.RULES) действуют всегда. E-mail участника организатор видит только с его явного согласия.
"""

from datetime import datetime, timedelta

from flask_babel import force_locale
from flask_babel import lazy_gettext as _l
from sqlalchemy import select

from ..extensions import db
from ..miniclub_guide import CLUB_FIELDS
from ..models import MiniClub, MiniClubMember, MiniClubReport, User
from ..models.miniclubs import JOIN_MODES, TOPICS
from ..utils import utcnow
from .audit import audit
from .mailer import send_mail

NAME_MAX = 120
ANNOUNCEMENT_MAX = 1000
ANNOUNCEMENT_INTERVAL = timedelta(hours=24)
MAX_CAPACITY = 200
REPORT_MAX = 1000

ANNOUNCE_SUBJECT = _l("Message from the mini-club %(club)s")
ANNOUNCE_BODY = _l("%(organizer)s, the organiser of the mini-club \"%(club)s\", writes to the members:\n\n%(text)s\n\n"
                   "This message was sent by a member, not by the club. The club is only an online platform. "
                   "You can leave the mini-club or report it here: %(url)s")
REQUEST_SUBJECT = _l("New request for your mini-club %(club)s")
REQUEST_BODY = _l("%(member)s would like to join your mini-club \"%(club)s\".\nPlease answer here: %(url)s")
DECISION_SUBJECT = _l("Your request for %(club)s")
DECISION_ACCEPTED = _l("Good news: you are now a member of the mini-club \"%(club)s\".\n%(url)s")
DECISION_DECLINED = _l("Unfortunately the organiser of \"%(club)s\" could not accept your request this time.\n%(url)s")


class MiniClubError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_club_form(form) -> tuple[dict, list[str]]:
    errors = []
    data = {
        "name": form.get("name", "").strip(),
        "topic": form.get("topic", ""),
        "city": form.get("city", "").strip()[:80],
        "online": bool(form.get("online")),
        "join_mode": form.get("join_mode", ""),
    }
    if not data["name"] or len(data["name"]) > NAME_MAX:
        errors.append("name")
    if data["topic"] not in TOPICS:
        errors.append("topic")
    if data["join_mode"] not in JOIN_MODES:
        errors.append("join_mode")
    for field in CLUB_FIELDS:
        value = form.get(field.key, "").strip()
        if (field.required and not value) or len(value) > field.max_length:
            errors.append(field.key)
        data[field.key] = value
    if not data["city"] and not data["online"]:
        errors.append("city")
    data["capacity"] = None
    raw = form.get("capacity", "").strip()
    if raw:
        try:
            capacity = int(raw)
            if not 2 <= capacity <= MAX_CAPACITY:
                raise ValueError
            data["capacity"] = capacity
        except ValueError:
            errors.append("capacity")
    if not form.get("accept_rules"):
        errors.append("accept_rules")
    return data, errors


def member_count(club_id: int) -> int:
    return MiniClubMember.query.filter_by(club_id=club_id, status="member").count()


def places_left(club: MiniClub):
    if club.capacity is None:
        return None
    return max(0, club.capacity - member_count(club.id) - 1)  # организатор занимает одно место


def create_club(organizer, data) -> MiniClub:
    """Мини-клуб участника ждёт проверки; клуб администратора открывается сразу."""
    data = {key: value for key, value in data.items() if key != "accept_rules"}
    status = "open" if organizer.is_admin else "pending"
    club = MiniClub(organizer_id=organizer.id, status=status, **data)
    db.session.add(club)
    db.session.flush()
    audit("miniclub.created" if status == "open" else "miniclub.submitted", target=f"miniclub:{club.id}",
          actor_id=organizer.id)
    return club


def edit_club(organizer, club: MiniClub, data) -> None:
    if club.organizer_id != organizer.id:
        raise MiniClubError("forbidden")
    if club.status == "closed":
        raise MiniClubError("not_editable")
    for key, value in data.items():
        if key != "accept_rules":
            setattr(club, key, value)
    if not organizer.is_admin:
        club.status = "pending"
        club.rejection_reason = None
    audit("miniclub.edited", target=f"miniclub:{club.id}", actor_id=organizer.id)


def approve_club(admin, club: MiniClub) -> None:
    if club.status != "pending":
        raise MiniClubError("not_pending")
    club.status, club.rejection_reason = "open", None
    audit("miniclub.approved", target=f"miniclub:{club.id}", actor_id=admin.id)


def reject_club(admin, club: MiniClub, reason: str) -> None:
    if club.status != "pending":
        raise MiniClubError("not_pending")
    club.status = "rejected"
    club.rejection_reason = reason.strip()[:300] or None
    audit("miniclub.rejected", target=f"miniclub:{club.id}", details=club.rejection_reason or "", actor_id=admin.id)


def close_club(actor, club: MiniClub, reason: str = "") -> None:
    """Закрывает мини-клуб: организатор — свой, администратор — любой (например по жалобе)."""
    if club.organizer_id != actor.id and not getattr(actor, "is_admin", False):
        raise MiniClubError("forbidden")
    club.status = "closed"
    audit("miniclub.closed", target=f"miniclub:{club.id}", details=reason[:200], actor_id=actor.id)


def join(user, club_id: int, message: str, share_contact: bool) -> MiniClubMember:
    """Вступление или заявка. Вызывающий делает commit при успехе и rollback при MiniClubError."""
    if not share_contact:
        raise MiniClubError("contact_needed")
    club = db.session.execute(select(MiniClub).where(MiniClub.id == club_id).with_for_update()).scalar_one_or_none()
    if club is None:
        raise MiniClubError("not_found")
    if club.status != "open":
        raise MiniClubError("not_open")
    if club.organizer_id == user.id:
        raise MiniClubError("own_club")
    existing = MiniClubMember.query.filter_by(club_id=club.id, user_id=user.id).first()
    if existing is not None and existing.status in ("member", "pending"):
        raise MiniClubError("already_joined")
    if existing is not None and existing.status == "declined":
        raise MiniClubError("declined")
    if club.join_mode == "open" and club.capacity is not None and places_left(club) == 0:
        raise MiniClubError("full")
    status = "member" if club.join_mode == "open" else "pending"
    if existing is not None:  # вернулся после выхода
        existing.status, existing.message, existing.share_contact = status, message.strip()[:500], True
        existing.decided_at = utcnow() if status == "member" else None
        row = existing
    else:
        row = MiniClubMember(club_id=club.id, user_id=user.id, status=status, message=message.strip()[:500],
                             share_contact=True, decided_at=utcnow() if status == "member" else None)
        db.session.add(row)
    db.session.flush()
    audit("miniclub.joined" if status == "member" else "miniclub.requested", target=f"miniclub:{club.id}",
          actor_id=user.id)
    return row


def decide(organizer, row: MiniClubMember, accept: bool) -> None:
    club = row.club
    if club.organizer_id != organizer.id:
        raise MiniClubError("forbidden")
    if row.status != "pending":
        raise MiniClubError("already_answered")
    if accept and club.capacity is not None and places_left(club) == 0:
        raise MiniClubError("full")
    row.status = "member" if accept else "declined"
    row.decided_at = utcnow()
    audit("miniclub.request_answered", target=f"miniclub:{club.id}", details=f"{row.id} {row.status}",
          actor_id=organizer.id)


def leave(user, club_id: int) -> None:
    row = MiniClubMember.query.filter_by(club_id=club_id, user_id=user.id).first()
    if row is None or row.status not in ("member", "pending"):
        raise MiniClubError("not_member")
    row.status = "left"
    row.decided_at = utcnow()
    audit("miniclub.left", target=f"miniclub:{club_id}", actor_id=user.id)


def remove_member(organizer, row: MiniClubMember) -> None:
    if row.club.organizer_id != organizer.id:
        raise MiniClubError("forbidden")
    row.status = "left"
    row.decided_at = utcnow()
    audit("miniclub.member_removed", target=f"miniclub:{row.club_id}", details=f"user={row.user_id}",
          actor_id=organizer.id)


def club_url(club: MiniClub) -> str:
    from flask import current_app

    return f"{current_app.config['BASE_URL'].rstrip('/')}/miniclubs/{club.id}"


def announce(organizer, club: MiniClub, text: str, now: datetime | None = None) -> list[User]:
    """Объявление всем участникам (не чаще раза в сутки). Возвращает получателей: письма уходят после commit."""
    now = now or utcnow()
    if club.organizer_id != organizer.id:
        raise MiniClubError("forbidden")
    if club.status != "open":
        raise MiniClubError("not_open")
    text = text.strip()
    if not text:
        raise MiniClubError("empty")
    if len(text) > ANNOUNCEMENT_MAX:
        raise MiniClubError("too_long")
    if club.last_announcement_at and now - club.last_announcement_at < ANNOUNCEMENT_INTERVAL:
        raise MiniClubError("too_soon")
    recipients = [row.user for row in club.members if row.status == "member"]
    if not recipients:
        raise MiniClubError("nobody")
    club.last_announcement_at = now
    audit("miniclub.announced", target=f"miniclub:{club.id}", details=f"count={len(recipients)}",
          actor_id=organizer.id)
    return recipients


def send_announcement(club: MiniClub, text: str, recipients: list[User]) -> None:
    for user in recipients:
        if not user.email:
            continue
        with force_locale(user.locale or "de"):
            values = {"club": club.name, "organizer": club.organizer.first_name, "text": text.strip(),
                      "url": club_url(club)}
            send_mail(user.email, str(ANNOUNCE_SUBJECT) % values, str(ANNOUNCE_BODY) % values)


def notify_request(club: MiniClub, member: User) -> None:
    organizer = club.organizer
    if organizer.email:
        with force_locale(organizer.locale or "de"):
            values = {"club": club.name, "member": member.first_name, "url": club_url(club)}
            send_mail(organizer.email, str(REQUEST_SUBJECT) % values, str(REQUEST_BODY) % values)


def notify_decision(club: MiniClub, user: User, accepted: bool) -> None:
    if user.email:
        with force_locale(user.locale or "de"):
            values = {"club": club.name, "url": club_url(club)}
            body = DECISION_ACCEPTED if accepted else DECISION_DECLINED
            send_mail(user.email, str(DECISION_SUBJECT) % values, str(body) % values)


def report_club(reporter, club: MiniClub, reason: str) -> MiniClubReport:
    reason = reason.strip()
    if len(reason) < 10:
        raise MiniClubError("reason_short")
    if club.organizer_id == reporter.id:
        raise MiniClubError("own_club")
    report = MiniClubReport(club_id=club.id, reporter_id=reporter.id, reason=reason[:REPORT_MAX])
    db.session.add(report)
    db.session.flush()
    audit("miniclub.reported", target=f"miniclub:{club.id}", actor_id=reporter.id)
    return report


def handle_report(admin, report: MiniClubReport) -> None:
    report.handled = True
    audit("miniclub.report_handled", target=f"miniclub:{report.club_id}", actor_id=admin.id)
