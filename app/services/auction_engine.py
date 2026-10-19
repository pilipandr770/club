"""Движок аукциона: повышение и понижение цены. Платежей здесь нет: расчёты будут отдельным шагом
после проверки юристом (CLAUDE.md, раздел «Отделы»).

Суммы — центы, время — naive UTC. Ставки только добавляются. Принятие ставки идёт под блокировкой
строки лота (SELECT ... FOR UPDATE), чтобы две ставки не приняли одну цену.
"""

from datetime import datetime

from sqlalchemy import select

from ..extensions import db
from ..models import Bid, Lot
from ..utils import utcnow
from .audit import audit

MIN_DROP_INTERVAL_SECONDS = 60


class AuctionError(Exception):
    """Ставку нельзя принять. code — короткий код причины для показа участнику."""

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
    if lot.mode == "ascending" and lot.bids:
        last = lot.bids[-1]
        _close(lot, last.user_id, last.amount_cents)
    else:
        _close(lot, None, None)  # понижение без покупателя или повышение без ставок
    return True


def place_bid(lot_id: int, user, amount_cents: int, now: datetime | None = None) -> Bid:
    """Принимает ставку. Вызывающий код делает commit при успехе и rollback при AuctionError."""
    now = now or utcnow()
    lot = db.session.execute(
        select(Lot).where(Lot.id == lot_id).with_for_update()
    ).scalar_one_or_none()
    if lot is None:
        raise AuctionError("not_found")
    if lot.status != "open":
        raise AuctionError("closed")
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
        bid = Bid(lot=lot, user_id=user.id, amount_cents=price, created_at=now)
        db.session.add(bid)
        lot.current_price_cents = price
        _close(lot, user.id, price)
        return bid

    minimum = minimum_next_bid(lot.start_price_cents, lot.current_price_cents,
                               lot.step_cents, has_bids=bool(lot.bids))
    if amount_cents < minimum:
        raise AuctionError("bid_too_low")
    bid = Bid(lot=lot, user_id=user.id, amount_cents=amount_cents, created_at=now)
    db.session.add(bid)
    lot.current_price_cents = amount_cents
    return bid


def validate_lot_input(data: dict) -> list[str]:
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
    if mode == "descending":
        floor = data.get("floor_price_cents")
        interval = data.get("drop_interval_seconds")
        if floor is None or floor < 0 or (data.get("start_price_cents") is not None and floor > data["start_price_cents"]):
            errors.append("floor_price")
        if not interval or interval < MIN_DROP_INTERVAL_SECONDS:
            errors.append("drop_interval")
    return errors


def create_lot(admin, data: dict) -> Lot:
    lot = Lot(
        title=data["title"].strip()[:160],
        description=data.get("description", "").strip(),
        mode=data["mode"],
        start_price_cents=data["start_price_cents"],
        floor_price_cents=data.get("floor_price_cents") if data["mode"] == "descending" else None,
        step_cents=data["step_cents"],
        drop_interval_seconds=data.get("drop_interval_seconds") if data["mode"] == "descending" else None,
        current_price_cents=data["start_price_cents"],
        status="open",
        opens_at=data["opens_at"],
        ends_at=data["ends_at"],
        created_by_id=admin.id,
    )
    db.session.add(lot)
    db.session.flush()
    audit("auction.lot_created", target=f"lot:{lot.id}", details=f"mode={lot.mode}", actor_id=admin.id)
    return lot


def cancel_lot(admin, lot: Lot) -> None:
    if lot.status != "open":
        return
    lot.status = "closed"
    audit("auction.lot_cancelled", target=f"lot:{lot.id}", actor_id=admin.id)


def close_due_lots(now: datetime | None = None) -> int:
    """Закрывает лоты с истёкшим временем. Вызывается командой close-auctions из планировщика."""
    now = now or utcnow()
    lots = Lot.query.filter(Lot.status == "open", Lot.ends_at <= now).all()
    return sum(1 for lot in lots if finish_if_due(lot, now))
