from flask import Blueprint, jsonify, render_template
from sqlalchemy import text

from ...extensions import db

bp = Blueprint("public", __name__)


@bp.get("/")
def index():
    return render_template("public/index.html")


@bp.get("/healthz")
def healthz():
    """Проверка для healthcheck контейнера: приложение и база отвечают."""
    db.session.execute(text("SELECT 1"))
    return jsonify(status="ok")
