"""Письма аукциона: вас перебили, лот закрыт (победителю и продавцу). Язык письма — user.locale.

В письмах нет контактов другой стороны: их участники видят только в кабинете после сделки.
"""

from flask import current_app
from flask_babel import force_locale
from flask_babel import lazy_gettext as _l

from ..extensions import db
from ..models import Lot, User
from ..utils import format_eur, utcnow
from .audit import audit
from .mailer import send_mail

OUTBID_SUBJECT = _l("You have been outbid: %(title)s")
OUTBID_BODY = _l("Someone placed a higher bid on \"%(title)s\". The current price is %(price)s.\n"
                 "You can bid again here: %(url)s")
WON_SUBJECT = _l("You won: %(title)s")
WON_BODY = _l("Congratulations! You won \"%(title)s\" for %(price)s.\n"
              "The club does not handle payment. Please arrange the handover with the seller. "
              "Contact details are shown on the lot page: %(url)s")
SOLD_SUBJECT = _l("Your lot is sold: %(title)s")
SOLD_BODY = _l("Your lot \"%(title)s\" is sold for %(price)s.\n"
               "The club does not handle payment. Please arrange the handover with the buyer. "
               "Contact details are shown on the lot page: %(url)s")
UNSOLD_SUBJECT = _l("Your lot ended without a sale: %(title)s")
UNSOLD_BODY = _l("Your lot \"%(title)s\" ended without a buyer (no bids, or the reserve price was not reached).\n"
                 "%(url)s")


def _lot_url(lot: Lot) -> str:
    # Путь собираем вручную: письма уходят и из планировщика, где нет запроса и SERVER_NAME
    return f"{current_app.config['BASE_URL'].rstrip('/')}/auction/{lot.id}"


def _send(user: User | None, subject, body, **values) -> None:
    if user is None or not user.email:
        return
    with force_locale(user.locale or "de"):
        # Ленивые строки вычисляются здесь, уже в языке получателя
        send_mail(user.email, str(subject) % values, str(body) % values)


def notify_outbid(previous_user_id: int | None, lot: Lot) -> None:
    if previous_user_id is None:
        return
    _send(db.session.get(User, previous_user_id), OUTBID_SUBJECT, OUTBID_BODY,
          title=lot.title, price=format_eur(lot.current_price_cents), url=_lot_url(lot))


def notify_closed(lot: Lot) -> bool:
    """Рассылает итоги закрытого лота один раз (notified_at). Возвращает True, если письма отправлялись."""
    if lot.status != "closed" or lot.notified_at is not None:
        return False
    lot.notified_at = utcnow()
    audit("auction.notified", target=f"lot:{lot.id}", actor_id=None)
    db.session.commit()
    seller = db.session.get(User, lot.seller_id) if lot.seller_id else None
    values = dict(title=lot.title, price=format_eur(lot.winning_price_cents) if lot.winning_price_cents else "",
                  url=_lot_url(lot))
    if lot.winner_id:
        _send(db.session.get(User, lot.winner_id), WON_SUBJECT, WON_BODY, **values)
        _send(seller, SOLD_SUBJECT, SOLD_BODY, **values)
    else:
        _send(seller, UNSOLD_SUBJECT, UNSOLD_BODY, **values)
    return True
