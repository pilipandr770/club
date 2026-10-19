"""Все модели импортируются здесь, чтобы Flask-Migrate их видел."""

from .user import Consent, Profile, User

__all__ = ["User", "Profile", "Consent"]
