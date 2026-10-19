"""Стартапы: размещение, модерация, интерес инвесторов. Контакты основателя — только после принятого интереса."""

from datetime import datetime

from ..extensions import db
from ..models import InvestorInterest, Startup
from ..models.startups import CATEGORIES, STAGES
from ..utils import clean_url, eur_to_cents, utcnow
from .audit import audit

NAME_MAX = 120
TAGLINE_MAX = 160
DESCRIPTION_MAX = 3000
MESSAGE_MAX = 1000


class StartupError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_startup_form(form) -> tuple[dict, list[str]]:
    """Разбирает форму стартапа. Возвращает (данные, список полей с ошибками)."""
    errors = []
    data = {
        "name": form.get("name", "").strip(),
        "tagline": form.get("tagline", "").strip(),
        "description": form.get("description", "").strip(),
        "stage": form.get("stage", ""),
        "category": form.get("category", ""),
    }
    if not data["name"] or len(data["name"]) > NAME_MAX:
        errors.append("name")
    if len(data["tagline"]) > TAGLINE_MAX:
        errors.append("tagline")
    if not data["description"] or len(data["description"]) > DESCRIPTION_MAX:
        errors.append("description")
    if data["stage"] not in STAGES:
        errors.append("stage")
    if data["category"] not in CATEGORIES:
        errors.append("category")

    raw_goal = form.get("funding_goal", "").strip()
    data["funding_goal_cents"] = None
    if raw_goal:
        try:
            data["funding_goal_cents"] = eur_to_cents(raw_goal)
        except ValueError:
            errors.append("funding_goal")

    raw_url = form.get("website_url", "").strip()
    data["website_url"] = None
    if raw_url:
        cleaned = clean_url(raw_url)
        if cleaned is None:
            errors.append("website_url")
        else:
            data["website_url"] = cleaned[:300]
    return data, errors


def create_startup(owner, data) -> Startup:
    startup = Startup(owner_id=owner.id, status="pending", **data)
    db.session.add(startup)
    db.session.flush()
    audit("startup.submitted", target=f"startup:{startup.id}", actor_id=owner.id)
    return startup


def update_startup(owner, startup: Startup, data) -> None:
    """Любая правка снова отправляет стартап на модерацию."""
    if startup.owner_id != owner.id:
        raise StartupError("forbidden")
    for key, value in data.items():
        setattr(startup, key, value)
    startup.status = "pending"
    startup.rejection_reason = None
    startup.approved_at = None
    audit("startup.updated", target=f"startup:{startup.id}", actor_id=owner.id)


def approve(admin, startup: Startup) -> None:
    startup.status = "approved"
    startup.approved_at = utcnow()
    startup.rejection_reason = None
    audit("startup.approved", target=f"startup:{startup.id}", actor_id=admin.id)


def reject(admin, startup: Startup, reason: str) -> None:
    startup.status = "rejected"
    startup.rejection_reason = reason.strip()[:300] or None
    startup.approved_at = None
    audit("startup.rejected", target=f"startup:{startup.id}", details=startup.rejection_reason or "",
          actor_id=admin.id)


def express_interest(investor, startup: Startup, message: str, now: datetime | None = None) -> InvestorInterest:
    if startup.status != "approved":
        raise StartupError("not_available")
    if startup.owner_id == investor.id:
        raise StartupError("own_startup")
    existing = InvestorInterest.query.filter_by(startup_id=startup.id, investor_id=investor.id).first()
    if existing is not None:
        raise StartupError("already_sent")
    interest = InvestorInterest(startup_id=startup.id, investor_id=investor.id,
                                message=message.strip()[:MESSAGE_MAX], status="pending",
                                created_at=now or utcnow())
    db.session.add(interest)
    db.session.flush()
    audit("startup.interest", target=f"startup:{startup.id}", actor_id=investor.id)
    return interest


def respond_to_interest(owner, interest: InvestorInterest, accept: bool) -> None:
    if interest.startup.owner_id != owner.id:
        raise StartupError("forbidden")
    if interest.status != "pending":
        raise StartupError("already_answered")
    interest.status = "accepted" if accept else "declined"
    interest.responded_at = utcnow()
    audit("startup.interest_answered", target=f"startup:{interest.startup_id}",
          details=f"interest={interest.id} {interest.status}", actor_id=owner.id)


def visible_contact(viewer, startup: Startup):
    """Контакт основателя виден владельцу и инвестору с принятым интересом. Иначе None."""
    if viewer is None:
        return None
    if viewer.id == startup.owner_id:
        return startup.owner.email
    accepted = InvestorInterest.query.filter_by(
        startup_id=startup.id, investor_id=viewer.id, status="accepted"
    ).first()
    return startup.owner.email if accepted else None
