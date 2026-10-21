"""Движок аукциона: повышение и понижение цены, лоты участников, передача после победы.

Платежей здесь нет: деньги между покупателем и продавцом клуб не ведёт (расчёты будут отдельным шагом
после проверки юристом, CLAUDE.md, раздел «Отделы»). После победы стороны связываются напрямую.

Суммы — центы, время — naive UTC. Ставки только добавляются. Принятие ставки идёт под блокировкой
строки лота (SELECT ... FOR UPDATE), чтобы две ставки не приняли одну цену.
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from ..extensions import db
from ..models import Bid, Lot
from ..utils import utcnow
from .audit import audit

MIN_DROP_INTERVAL_SECONDS = 60
SNIPE_WINDOW = timedelta(minutes=2)  # ставка в последние 2 минуты продлевает торги
SNIPE_EXTENSION = timedelta(minutes=2)
MAX_EXTENSIONS = 10
MAX_DURATION = timedelta(days=30)


class AuctionError(Exception):
    """Действие нельзя выполнить. code — короткий код причины для показа участнику."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def descending_price_at(start_cents, floor_cents, step_cents, interval_seconds, opens_at, now):
    """Чистая функция: цена понижения в момент now. Цена не падает ниже floor."""
    elapsed = (now - opens_at).total_seconds()
    if elapsed <= 0:
        return start_cents
    steps = int(elapsed // interval_seconds)
    return max(floor_cents, start_cents - steps * step_cents)


def minimum_next_bid(start_cents, current_cents, step_cents, has_bids):
    """Чистая функция: минимальная сумма следующей ставки на повышение."""
    if not has_bids:
        return start_cents
    return current_cents + step_cents


def current_price(lot: Lot, now: datetime) -> int:
    if lot.mode == "descending":
        return descending_price_at(lot.start_price_cents, lot.floor_price_cents, lot.step_cents,
                                   lot.drop_interval_seconds, lot.opens_at, now)
    return lot.current_price_cents


def next_drop_at(lot: Lot, now: datetime):
    """Когда цена понижения упадёт в следующий раз (None, если уже на минимуме или лот не понижающий)."""
    if lot.mode != "descending" or current_price(lot, now) <= lot.floor_price_cents:
        return None
    elapsed = (now - lot.opens_at).total_seconds()
    if elapsed < 0:
        return lot.opens_at + timedelta(seconds=lot.drop_interval_seconds)
    steps = int(elapsed // lot.drop_interval_seconds) + 1
    return lot.opens_at + timedelta(seconds=steps * lot.drop_interval_seconds)


def reserve_met(lot: Lot) -> bool:
    """Достигнута ли резервная цена. Без резерва — всегда да. Сумму резерва участникам не показываем."""
    if lot.reserve_price_cents is None:
        return True
    return bool(lot.bids) and lot.current_price_cents >= lot.reserve_price_cents


def _close(lot: Lot, winner_id, price_cents):
    lot.status = "closed"
    lot.winner_id = winner_id
    lot.winning_price_cents = price_cents
    audit("auction.closed", target=f"lot:{lot.id}",
          details=f"winner={winner_id} price={price_cents}", actor_id=None)


def finish_if_due(lot: Lot, now: datetime) -> bool:
    """Закрывает лот, если его время вышло. Возвращает True, если закрыт."""
    if lot.status != "open" or now < lot.ends_at:
        return False
    if lot.mode == "ascending" and lot.bids and reserve_met(lot):
        last = lot.bids[-1]
        _close(lot, last.user_id, last.amount_cents)
    else:
        _close(lot, None, None)  # без ставок, без покупателя или резерв не достигнут
    return True


def place_bid(lot_id: int, user, amount_cents: int, now: datetime | None = None, share_contact: bool = True) -> Bid:
    """Принимает ставку. Вызывающий код делает commit при успехе и rollback при AuctionError.

    share_contact — участник разрешил продавцу увидеть его контакты, если он выиграет.
    """
    now = now or utcnow()
    lot = db.session.execute(
        select(Lot).where(Lot.id == lot_id).with_for_update()
    ).scalar_one_or_none()
    if lot is None:
        raise AuctionError("not_found")
    if lot.status != "open":
        raise AuctionError("closed")
    if lot.seller_id is not None and lot.seller_id == user.id:
        raise AuctionError("own_lot")
    if now < lot.opens_at:
        raise AuctionError("not_started")
    if now >= lot.ends_at:
        finish_if_due(lot, now)
        raise AuctionError("closed")

    if lot.mode == "descending":
        price = current_price(lot, now)
        # Покупатель соглашается на цену не выше той, которую видел; платит текущую
        if amount_cents < price:
            raise AuctionError("price_too_low")
        bid = Bid(lot=lot, user_id=user.id, amount_cents=price, created_at=now, share_contact=share_contact)
        db.session.add(bid)
        lot.current_price_cents = price
        _close(lot, user.id, price)
        bid.previous_leader_id = None
        return bid

    minimum = minimum_next_bid(lot.start_price_cents, lot.current_price_cents,
                               lot.step_cents, has_bids=bool(lot.bids))
    if amount_cents < minimum:
        raise AuctionError("bid_too_low")
    previous_leader = lot.bids[-1].user_id if lot.bids else None
    bid = Bid(lot=lot, user_id=user.id, amount_cents=amount_cents, created_at=now, share_contact=share_contact)
    db.session.add(bid)
    lot.current_price_cents = amount_cents
    # Защита от «снайперов»: ставка в последние минуты немного продлевает торги
    if lot.ends_at - now <= SNIPE_WINDOW and lot.extended_count < MAX_EXTENSIONS:
        lot.ends_at = lot.ends_at + SNIPE_EXTENSION
        lot.extended_count += 1
    bid.previous_leader_id = previous_leader if previous_leader != user.id else None
    return bid


def validate_lot_input(data: dict, now: datetime | None = None, member: bool = False) -> list[str]:
    """Проверяет поля лота. Возвращает список кодов ошибок (пусто — всё верно)."""
    errors = []
    if not data.get("title", "").strip():
        errors.append("title")
    mode = data.get("mode")
    if mode not in ("ascending", "descending"):
        errors.append("mode")
    if data.get("start_price_cents") is None or data["start_price_cents"] < 0:
        errors.append("start_price")
    if not data.get("step_cents") or data["step_cents"] <= 0:
        errors.append("step")
    if data.get("opens_at") is None or data.get("ends_at") is None or data["ends_at"] <= data["opens_at"]:
        errors.append("times")
    elif member and data["ends_at"] - data["opens_at"] > MAX_DURATION:
        errors.append("times")
    if mode == "descending":
        floor = data.get("floor_price_cents")
        interval = data.get("drop_interval_seconds")
        if floor is None or floor < 0 or (data.get("start_price_cents") is not None and floor > data["start_price_cents"]):
            errors.append("floor_price")
        if not interval or interval < MIN_DROP_INTERVAL_SECONDS:
            errors.append("drop_interval")
    reserve = data.get("reserve_price_cents")
    if reserve is not None and mode == "ascending":
        if data.get("start_price_cents") is not None and reserve < data["start_price_cents"]:
            errors.append("reserve_price")
    return errors


def _lot_fields(data: dict) -> dict:
    ascending = data["mode"] == "ascending"
    return dict(
        title=data["title"].strip()[:160],
        description=data.get("description", "").strip(),
        mode=data["mode"],
        start_price_cents=data["start_price_cents"],
        floor_price_cents=None if ascending else data.get("floor_price_cents"),
        step_cents=data["step_cents"],
        drop_interval_seconds=None if ascending else data.get("drop_interval_seconds"),
        current_price_cents=data["start_price_cents"],
        opens_at=data["opens_at"],
        ends_at=data["ends_at"],
        condition=data.get("condition") or None,
        pickup_info=data.get("pickup_info", "").strip(),
        reserve_price_cents=data.get("reserve_price_cents") if ascending else None,
    )


def create_lot(admin, data: dict) -> Lot:
    """Лот администратора: сразу открыт."""
    lot = Lot(status="open", created_by_id=admin.id, **_lot_fields(data))
    db.session.add(lot)
    db.session.flush()
    audit("auction.lot_created", target=f"lot:{lot.id}", details=f"mode={lot.mode}", actor_id=admin.id)
    return lot


def create_member_lot(seller, data: dict) -> Lot:
    """Лот участника: ждёт проверки. Сроки сдвигаются при публикации, длительность сохраняется."""
    lot = Lot(status="pending", created_by_id=seller.id, seller_id=seller.id, seller_contact_consent=True,
              **_lot_fields(data))
    db.session.add(lot)
    db.session.flush()
    audit("auction.lot_submitted", target=f"lot:{lot.id}", details=f"mode={lot.mode}", actor_id=seller.id)
    return lot


def edit_member_lot(seller, lot: Lot, data: dict) -> None:
    """Правка своего лота до публикации (пока нет торгов). После правки лот снова на проверке."""
    if lot.seller_id != seller.id:
        raise AuctionError("forbidden")
    if lot.status not in ("pending", "rejected") or lot.bids:
        raise AuctionError("not_editable")
    for key, value in _lot_fields(data).items():
        setattr(lot, key, value)
    lot.status = "pending"
    lot.rejection_reason = None
    audit("auction.lot_edited", target=f"lot:{lot.id}", actor_id=seller.id)


def approve_lot(admin, lot: Lot, now: datetime | None = None) -> None:
    if lot.status != "pending":
        raise AuctionError("not_pending")
    now = now or utcnow()
    duration = lot.ends_at - lot.opens_at
    lot.opens_at, lot.ends_at = now, now + duration
    lot.status = "open"
    lot.rejection_reason = None
    audit("auction.lot_approved", target=f"lot:{lot.id}", actor_id=admin.id)


def reject_lot(admin, lot: Lot, reason: str) -> None:
    if lot.status != "pending":
        raise AuctionError("not_pending")
    lot.status = "rejected"
    lot.rejection_reason = reason.strip()[:300] or None
    audit("auction.lot_rejected", target=f"lot:{lot.id}", details=lot.rejection_reason or "", actor_id=admin.id)


def cancel_lot(actor, lot: Lot) -> None:
    """Администратор закрывает любой открытый или ожидающий лот; продавец — свой, пока нет ставок."""
    if lot.status not in ("open", "pending"):
        return
    if not getattr(actor, "is_admin", False):
        if lot.seller_id != actor.id:
            raise AuctionError("forbidden")
        if lot.bids:
            raise AuctionError("has_bids")
    lot.status = "closed"
    audit("auction.lot_cancelled", target=f"lot:{lot.id}", actor_id=actor.id)


def close_due_lots(now: datetime | None = None) -> list[Lot]:
    """Закрывает лоты с истёкшим временем. Вызывается командой close-auctions из планировщика."""
    now = now or utcnow()
    lots = Lot.query.filter(Lot.status == "open", Lot.ends_at <= now).all()
    return [lot for lot in lots if finish_if_due(lot, now)]


# --------------------------------------------------------------------------- после победы
def is_sold(lot: Lot) -> bool:
    return lot.status == "closed" and lot.winner_id is not None


def mark_shipped(seller, lot: Lot) -> None:
    """Продавец отмечает, что товар передан или отправлен."""
    if lot.seller_id is None or lot.seller_id != seller.id:
        raise AuctionError("forbidden")
    if not is_sold(lot):
        raise AuctionError("not_sold")
    if lot.fulfillment is None:
        lot.fulfillment = "shipped"
        audit("auction.shipped", target=f"lot:{lot.id}", actor_id=seller.id)


def confirm_received(buyer, lot: Lot) -> None:
    """Покупатель подтверждает получение: сделка закрыта."""
    if not is_sold(lot):
        raise AuctionError("not_sold")
    if lot.winner_id != buyer.id:
        raise AuctionError("forbidden")
    if lot.fulfillment != "received":
        lot.fulfillment = "received"
        audit("auction.received", target=f"lot:{lot.id}", actor_id=buyer.id)


def counterparty(viewer, lot: Lot):
    """Контакт другой стороны сделки: покупатель видит продавца, продавец — покупателя.

    Только после закрытия лота с победителем и только с согласий обеих сторон (флажки при ставке и при создании).
    Возвращает (имя, e-mail) или None.
    """
    if not is_sold(lot) or viewer is None:
        return None
    winning_bid = lot.bids[-1] if lot.bids else None
    if lot.seller_id is not None and viewer.id == lot.winner_id and lot.seller_contact_consent and lot.seller:
        return lot.seller.first_name, lot.seller.email
    if lot.seller_id is not None and viewer.id == lot.seller_id and winning_bid is not None \
            and winning_bid.user_id == lot.winner_id and winning_bid.share_contact and lot.winner:
        return lot.winner.first_name, lot.winner.email
    return None
