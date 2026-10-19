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
