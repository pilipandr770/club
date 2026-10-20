from flask import current_app

from ..extensions import db
from ..models import Consent
from ..models.user import CONSENT_KINDS


def record_consent(user, kind: str, granted: bool, source: str = "web") -> None:
    """Единственный путь записи согласия: строка добавляется, существующие не меняются."""
    if kind not in CONSENT_KINDS:
        raise ValueError(f"Unbekannte Einwilligungsart: {kind}")
    db.session.add(
        Consent(
            user=user,
            kind=kind,
            granted=granted,
            version=current_app.config["CONSENT_VERSION"],
            source=source,
        )
    )


def latest_consent(user, kind: str):
    """Последнее решение участника по виду согласия: True, False или None (решения ещё не было)."""
    from ..models import Consent

    row = (Consent.query.filter_by(user_id=user.id, kind=kind)
           .order_by(Consent.created_at.desc(), Consent.id.desc()).first())
    return None if row is None else row.granted

