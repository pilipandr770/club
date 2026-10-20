"""Услуги партнёров: каталог, запросы участников, письмо партнёру.

Письмо партнёру уходит только если участник отметил «передать мои контакты партнёру».
Без этой отметки партнёр получает текст запроса, но не адрес участника.
"""

import re
import uuid

from ..extensions import db
from ..models import Service, ServiceInquiry
from ..utils import utcnow
from .audit import audit
from .mailer import send_mail

MESSAGE_MAX = 2000


class CatalogError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]
    return base or uuid.uuid4().hex[:12]


def unique_slug(title: str, exclude_id=None) -> str:
    slug = slugify(title)
    candidate, counter = slug, 2
    while True:
        query = Service.query.filter_by(slug=candidate)
        if exclude_id is not None:
            query = query.filter(Service.id != exclude_id)
        if query.first() is None:
            return candidate
        candidate = f"{slug}-{counter}"
        counter += 1


def save_service(admin, service: Service | None, data: dict) -> Service:
    is_new = service is None
    if is_new:
        service = Service(slug=unique_slug(data["title"]))
        db.session.add(service)
    for key, value in data.items():
        setattr(service, key, value)
    db.session.flush()
    audit("service.created" if is_new else "service.updated", target=f"service:{service.id}", actor_id=admin.id)
    return service


def request_service(user, service: Service, message: str, share_contact: bool) -> ServiceInquiry:
    if not service.active:
        raise CatalogError("not_available")
    message = message.strip()[:MESSAGE_MAX]
    if not message:
        raise CatalogError("empty_message")
    inquiry = ServiceInquiry(service_id=service.id, user_id=user.id, message=message,
                             share_contact=share_contact, status="new", created_at=utcnow())
    db.session.add(inquiry)
    db.session.flush()
    audit("service.inquiry", target=f"service:{service.id}", details=f"share_contact={share_contact}",
          actor_id=user.id)
    if service.provider_email:
        from flask_babel import force_locale, gettext

        with force_locale("de"):  # Партнёр — немецкоязычный, письмо на немецком
            lines = [gettext("New request through the club for: %(title)s", title=service.title), "", message]
            if share_contact:
                lines += ["", gettext("Member's contact: %(email)s", email=user.email)]
            else:
                lines += ["", gettext("The member did not share contact details.")]
            subject = gettext("Request: %(title)s", title=service.title)
            body = "\n".join(lines)
        send_mail(service.provider_email, subject, body)
    return inquiry


def set_inquiry_status(admin, inquiry: ServiceInquiry, status: str) -> None:
    if status not in ("new", "contacted", "won", "lost"):
        raise CatalogError("bad_status")
    inquiry.status = status
    audit("service.inquiry_status", target=f"inquiry:{inquiry.id}", details=status, actor_id=admin.id)


def status_label(status: str) -> str:
    from flask_babel import gettext as _

    return {
        "new": _("New"),
        "contacted": _("Contacted"),
        "won": _("Done"),
        "lost": _("Closed"),
    }.get(status, status)

