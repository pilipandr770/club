from flask import has_request_context
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def audit(action: str, target: str = "", details: str = "", actor_id: int | None = None) -> None:
    """Добавляет запись в журнал. Коммит делает вызывающий код вместе с основным действием."""
    if actor_id is None and has_request_context() and current_user.is_authenticated:
        actor_id = current_user.id
    db.session.add(AuditLog(actor_id=actor_id, action=action, target=target, details=details[:2000]))
