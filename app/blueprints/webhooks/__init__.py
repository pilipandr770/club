import logging

from flask import Blueprint, current_app, request

from ...extensions import csrf, db
from ...services.verification import handle_event

bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")
log = logging.getLogger(__name__)


@bp.route("/stripe", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    """Вебхук Stripe. Подпись проверяется до любых изменений; без подписи ничего не меняется."""
    signature = request.headers.get("Stripe-Signature", "")
    provider = current_app.extensions["identity_provider"]
    try:
        event = provider.construct_event(request.get_data(), signature)
    except Exception:
        log.warning("Ungültige Stripe-Signatur", exc_info=True)
        return ("invalid signature", 400)

    try:
        outcome = handle_event(event)
        db.session.commit()
    except Exception:
        # 500: Stripe повторит доставку, событие ещё не записано как обработанное
        db.session.rollback()
        log.exception("Stripe-Ereignis %s konnte nicht verarbeitet werden", event.get("id"))
        return ("processing failed", 500)

    log.info("Stripe-Ereignis %s: %s", event.get("id"), outcome)
    return ("ok", 200)
