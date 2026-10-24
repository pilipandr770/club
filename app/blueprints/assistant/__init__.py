"""Личный помощник Clubi: один JSON-эндпоинт. Страница и анкета определяются на сервере (services/assistant.py)."""

from flask import Blueprint, jsonify, request
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db, limiter
from ...services.audit import audit
from ...services.consents import latest_consent, record_consent
from ...services.ai.http import AIUnavailable
from ...services.ai.speech import MAX_BYTES, transcribe
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


@bp.route("/voice-consent", methods=["POST"])
@requires_feature("assistant.use")
@limiter.limit("10 per minute")
def voice_consent():
    """Согласие на отправку записи провайдеру распознавания речи. Отзывается на странице «Публикация»."""
    record_consent(current_user, "voice", True)
    audit("voice.consent", target=f"user:{current_user.id}", actor_id=current_user.id)
    db.session.commit()
    return jsonify(ok=True)


@bp.route("/transcribe", methods=["POST"])
@requires_feature("assistant.use")
@limiter.limit("10 per minute")
@limiter.limit("100 per day")
def transcribe_view():
    """Голос в текст. Аудио в памяти запроса, на диск и в базу не попадает; текст возвращаем в поле ввода."""
    if latest_consent(current_user, "voice") is not True:
        return jsonify(error=_("Please agree to voice input first."), code="consent"), 403
    upload = request.files.get("audio")
    data = upload.read(MAX_BYTES + 1) if upload else b""
    if not data:
        return jsonify(error=_("No recording received. Please try again."), code="empty"), 400
    if len(data) > MAX_BYTES:
        return jsonify(error=_("The recording is too long. Please keep it under a minute."), code="too_large"), 413
    try:
        text = transcribe(data, upload.mimetype)
    except AIUnavailable as exc:
        message = (_("This audio format is not supported.") if str(exc) == "bad_audio"
                   else _("Voice input is not available right now. Please type your question."))
        return jsonify(error=message, code="unavailable"), 400 if str(exc) == "bad_audio" else 503
    if not text:
        return jsonify(error=_("I could not hear anything. Please try again."), code="silence"), 422
    return jsonify(text=text[:1000])
