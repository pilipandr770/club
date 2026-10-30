"""Все модели импортируются здесь, чтобы Flask-Migrate их видел."""

from .ai_usage import AiUsage
from .analytics import VisitSeen, VisitStat
from .auction import Bid, Lot
from .catalog import Service, ServiceInquiry
from .invites import Invite
from .events import Event, EventInvite, EventRsvp
from .laws import Law, LawChunk, LawSection, LawSyncState
from .matching import IntroRequest, Match
from .miniclubs import MiniClub, MiniClubMember, MiniClubReport
from .startups import InvestorInterest, Startup
from .travel import Booking, Trip
from .tools import ToolRun
from .system import AuditLog, WebhookEvent
from .user import CONSENT_KINDS, Consent, Profile, User

__all__ = ["User", "Profile", "Consent", "CONSENT_KINDS", "AuditLog", "WebhookEvent", "Lot", "Bid", "Startup", "InvestorInterest", "Trip", "Booking", "Match", "IntroRequest", "Service", "ServiceInquiry", "ToolRun", "Event", "EventRsvp", "EventInvite", "MiniClub", "MiniClubMember", "MiniClubReport", "Law", "LawSection", "LawChunk", "LawSyncState", "VisitStat", "VisitSeen", "AiUsage", "Invite"]
