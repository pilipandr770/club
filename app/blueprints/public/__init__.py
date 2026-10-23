from datetime import timedelta

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import text

from ...extensions import db
from ..._compat import safe_next
from ...utils import utcnow

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    return render_template("public/index.html")


@bp.post("/language")
def language():
    locale = request.form.get("locale", "")
    if locale not in current_app.config["LOCALES_ENABLED"]:
        abort(400)
    if current_user.is_authenticated:
        current_user.locale = locale
        db.session.commit()
    response = redirect(safe_next(request.form.get("next")) or url_for("public.index"), code=303)
    response.set_cookie("locale", locale, max_age=365 * 24 * 60 * 60, httponly=True,
                        secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return response


@bp.get("/healthz")
def healthz():
    """Проверка для healthcheck контейнера: приложение и база отвечают."""
    db.session.execute(text("SELECT 1"))
    return jsonify(status="ok")


@bp.get("/.well-known/security.txt")
def security_txt():
    """Контакт для сообщений об уязвимостях (RFC 9116). Срок действия всегда на полгода вперёд."""
    cfg = current_app.config
    expires = (utcnow() + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"Contact: mailto:{cfg.get('SECURITY_CONTACT', 'info@andrii-it.de')}",
             f"Expires: {expires}",
             "Preferred-Languages: de, en",
             f"Canonical: {cfg['BASE_URL'].rstrip('/')}/.well-known/security.txt"]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")
