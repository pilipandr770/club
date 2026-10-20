"""Все модели импортируются здесь, чтобы Flask-Migrate их видел."""

from .auction import Bid, Lot
from .startups import InvestorInterest, Startup
from .travel import Booking, Trip
from .system import AuditLog, WebhookEvent
from .user import CONSENT_KINDS, Consent, Profile, User

__all__ = ["User", "Profile", "Consent", "CONSENT_KINDS", "AuditLog", "WebhookEvent", "Lot", "Bid", "Startup", "InvestorInterest", "Trip", "Booking"]
