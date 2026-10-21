"""Личный помощник Clubi: один JSON-эндпоинт. Страница и анкета определяются на сервере (services/assistant.py)."""

from flask import Blueprint, jsonify, request
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import limiter
from ...services.assistant import AssistantError, ask, questions_left
from ...services.entitlements import requires_feature

bp = Blueprint("assistant", __name__, url_prefix="/assistant")


def _error_text(code):
    return {
        "empty": _("Please write a question."),
        "unavailable": _("The assistant is not available right now. Please try again later."),
        "limit": _("You have reached today's limit of questions. It renews tomorrow."),
    }.get(code, _("Something went wrong. Please try again."))


@bp.route("/ask", methods=["POST"])
@requires_feature("assistant.use")
@limiter.limit("12 per minute")
def ask_view():
    data = request.get_json(silent=True) or {}
    history = data.get("history") if isinstance(data.get("history"), list) else []
    try:
        answer, label = ask(current_user, str(data.get("message", "")), str(data.get("path", "")), history)
    except AssistantError as exc:
        return jsonify(error=_error_text(exc.code), code=exc.code), 429 if exc.code == "limit" else 400
    return jsonify(answer=answer, context=label or "", left=questions_left(current_user))
