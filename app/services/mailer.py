"""E-Mail-Versand по SMTP. Без SMTP_HOST письмо только записывается в лог (локальная разработка)."""

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger(__name__)


def send_mail(to: str, subject: str, body: str) -> bool:
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        log.warning("MAIL (nicht versendet, SMTP fehlt) an %s | %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as smtp:
            if cfg.get("SMTP_TLS"):
                smtp.starttls()
            if cfg.get("SMTP_USER"):
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return True
    except Exception:
        log.exception("Mailversand fehlgeschlagen an %s", to)
        return False
