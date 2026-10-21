"""Права участника: экспорт (Art. 15/20) и удаление (Art. 17) — самообслуживание."""

from datetime import datetime

from ..extensions import db
from ..models import Consent
from .audit import audit


def _iso(value):
    return value.isoformat(timespec="seconds") if isinstance(value, datetime) else None


def export_user(user) -> dict:
    profile = user.profile
    return {
        "konto": {
            "email": user.email,
            "vorname": user.first_name,
            "nachname": user.last_name,
            "status": user.status,
            "tarif": user.plan,
            "erstellt": _iso(user.created_at),
            "letzter_login": _iso(user.last_login_at),
            "e_mail_bestaetigt": _iso(user.email_confirmed_at),
        },
        "profil": None if profile is None else {
            "land": profile.country,
            "wohnsitzland": profile.residence_country,
        },
        "identitaetspruefung": {
            "status": user.verification_status,
            "verifiziert_am": _iso(user.verified_at),
            "hinweis": "Ausweisdokumente, Selfies und Geburtsdaten liegen nicht beim Club, "
                       "sondern beim Prüfdienst Stripe. Der Club speichert nur den Status.",
        },
        "einwilligungen": [
            {"art": c.kind, "erteilt": c.granted, "version": c.version, "zeit": _iso(c.created_at)}
            for c in Consent.query.filter_by(user_id=user.id).order_by(Consent.created_at)
        ],
    }


def delete_user(user, provider) -> None:
    """Сначала удаляем данные у провайдера, потом у нас. Если провайдер недоступен, ничего не удаляем."""
    if user.stripe_verification_session_id:
        provider.redact(user.stripe_verification_session_id)
    from flask import current_app

    from ..models import Startup

    # Файлы лежат вне БД: ключи запоминаем до удаления строк и стираем после commit
    keys = [user.profile.photo_key] if user.profile and user.profile.photo_key else []
    for startup in Startup.query.filter_by(owner_id=user.id).all():
        keys += [key for key in (startup.logo_key, startup.deck_key) if key]
    audit("account.deleted", target=f"user:{user.id}", actor_id=user.id)
    db.session.delete(user)  # профиль, согласия и стартапы удаляются каскадом
    db.session.commit()
    storage = current_app.extensions.get("media")
    if storage is not None:
        for key in keys:
            storage.delete(key)
