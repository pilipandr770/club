"""Подписанные одноразовые ссылки: подтверждение почты."""

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

EMAIL_CONFIRM_SALT = "email-confirm"
EMAIL_CONFIRM_MAX_AGE = 3 * 24 * 3600  # 3 дня


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=EMAIL_CONFIRM_SALT)


def make_email_token(user) -> str:
    return _serializer().dumps({"uid": user.id, "email": user.email})


def read_email_token(token: str):
    """Возвращает словарь {uid, email} или None, если ссылка повреждена или истекла."""
    try:
        return _serializer().loads(token, max_age=EMAIL_CONFIRM_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return None
