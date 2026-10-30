"""Двухфакторный вход по одноразовым кодам приложения-аутентификатора (TOTP, RFC 6238) и резервные коды.

- Секрет хранится в БД только зашифрованным (Fernet, ключ выводится из SECRET_KEY); открытым он виден лишь при настройке.
- Каждый принятый шаг по времени запоминается: тот же код второй раз не проходит (защита от подсмотренного кода).
- Резервных кодов 8, хранятся хеши (HMAC), каждый одноразовый. Новая выдача отменяет старые.
- Админу нельзя отключить второй фактор самому; сбросить его другому может только другой админ с пометкой.
"""

import base64
import hashlib
import hmac
import re
import secrets
import time

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from markupsafe import Markup

from ..extensions import db
from ..models.twofactor import RecoveryCode
from ..models.user import User
from ..utils import utcnow
from .audit import audit

STEP = 30
RECOVERY_COUNT = 8
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # без похожих символов: 0/o, 1/l/i


class TwoFactorError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _secret_key() -> bytes:
    return current_app.config["SECRET_KEY"].encode()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"totp:" + _secret_key()).digest()))


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user: User, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=current_app.config.get("TOTP_ISSUER", "Club"))


def qr_svg(uri: str) -> Markup:
    """QR-код как встроенный SVG без стилей и скриптов (CSP это допускает)."""
    return Markup(segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#000000", light="#ffffff"))


def _clean_code(raw: str) -> str:
    return re.sub(r"[\s-]", "", (raw or "")).lower()


def _decrypt(user: User) -> str | None:
    try:
        return _fernet().decrypt(user.totp_secret_enc.encode()).decode() if user.totp_secret_enc else None
    except InvalidToken:
        return None


def _matching_step(secret: str, code: str, after: int | None) -> int | None:
    """Шаг времени, которому соответствует код (допускаем соседние, чтобы пережить расхождение часов), или None."""
    if not re.fullmatch(r"\d{6}", code):
        return None
    totp = pyotp.TOTP(secret, interval=STEP)
    now_step = int(time.time()) // STEP
    for step in (now_step, now_step - 1, now_step + 1):
        if hmac.compare_digest(totp.at(step * STEP), code) and (after is None or step > after):
            return step
    return None


def verify(user: User, raw_code: str) -> bool:
    """Код приложения верен и ещё не использовался. Запоминает шаг, поэтому вызывающий код должен сделать commit."""
    secret = _decrypt(user)
    if not user.has_two_factor or not secret:
        return False
    step = _matching_step(secret, _clean_code(raw_code), user.totp_last_step)
    if step is None:
        return False
    user.totp_last_step = step
    return True


def _hash(code: str) -> str:
    return hmac.new(_secret_key(), _clean_code(code).encode(), hashlib.sha256).hexdigest()


def generate_recovery_codes(user: User) -> list[str]:
    RecoveryCode.query.filter_by(user_id=user.id).delete()
    codes = ["".join(secrets.choice(_ALPHABET) for _ in range(10)) for _ in range(RECOVERY_COUNT)]
    for code in codes:
        db.session.add(RecoveryCode(user_id=user.id, code_hash=_hash(code)))
    return [f"{code[:5]}-{code[5:]}" for code in codes]


def use_recovery_code(user: User, raw_code: str) -> bool:
    cleaned = _clean_code(raw_code)
    if len(cleaned) != 10:
        return False
    row = RecoveryCode.query.filter_by(user_id=user.id, code_hash=_hash(cleaned), used_at=None).first()
    if row is None:
        return False
    row.used_at = utcnow()
    audit("2fa.recovery_code_used", target=f"user:{user.id}", actor_id=user.id,
          details=f"{RecoveryCode.query.filter_by(user_id=user.id, used_at=None).count()} left")
    return True


def recovery_codes_left(user: User) -> int:
    return RecoveryCode.query.filter_by(user_id=user.id, used_at=None).count()


def enable(user: User, secret: str, raw_code: str) -> list[str]:
    """Включает второй фактор после ввода верного кода из приложения. Возвращает резервные коды (показываются один раз)."""
    step = _matching_step(secret, _clean_code(raw_code), None)
    if step is None:
        raise TwoFactorError("bad_code")
    user.totp_secret_enc = _fernet().encrypt(secret.encode()).decode()
    user.totp_enabled_at = utcnow()
    user.totp_last_step = step
    codes = generate_recovery_codes(user)
    audit("2fa.enabled", target=f"user:{user.id}", actor_id=user.id)
    return codes


def _clear(user: User) -> None:
    user.totp_secret_enc = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    RecoveryCode.query.filter_by(user_id=user.id).delete()


def disable(user: User, password: str, raw_code: str) -> None:
    """Самостоятельное отключение: нужны пароль и текущий код. Админам нельзя: у них второй фактор обязателен."""
    if user.is_admin and current_app.config.get("ADMIN_2FA_REQUIRED", True):
        raise TwoFactorError("admin_required")
    if not user.check_password(password):
        raise TwoFactorError("bad_password")
    if not (verify(user, raw_code) or use_recovery_code(user, raw_code)):
        raise TwoFactorError("bad_code")
    _clear(user)
    audit("2fa.disabled", target=f"user:{user.id}", actor_id=user.id)


def regenerate_codes(user: User, password: str, raw_code: str) -> list[str]:
    if not user.check_password(password):
        raise TwoFactorError("bad_password")
    if not verify(user, raw_code):
        raise TwoFactorError("bad_code")
    codes = generate_recovery_codes(user)
    audit("2fa.codes_regenerated", target=f"user:{user.id}", actor_id=user.id)
    return codes


def admin_reset(admin: User, user: User, note: str) -> None:
    """Сброс для человека, потерявшего телефон и резервные коды. Себя сбросить нельзя; после сброса нужна новая настройка."""
    if admin.id == user.id:
        raise TwoFactorError("self")
    note = " ".join((note or "").split())
    if len(note) < 5:
        raise TwoFactorError("note_required")
    if not user.has_two_factor:
        raise TwoFactorError("not_enabled")
    _clear(user)
    audit("admin.2fa_reset", target=f"user:{user.id}", actor_id=admin.id, details=note[:300])
