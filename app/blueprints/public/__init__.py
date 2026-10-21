from datetime import timedelta

from flask import Blueprint, Response, current_app, jsonify, render_template
from sqlalchemy import text

from ...extensions import db
from ...utils import utcnow

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    return render_template("public/index.html")


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
