"""Все модели импортируются здесь, чтобы Flask-Migrate их видел."""

from .system import AuditLog, WebhookEvent
from .user import CONSENT_KINDS, Consent, Profile, User

__all__ = ["User", "Profile", "Consent", "CONSENT_KINDS", "AuditLog", "WebhookEvent"]
