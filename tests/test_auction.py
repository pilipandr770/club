from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Bid, Lot, User
from app.services.auction_engine import (
    AuctionError,
    close_due_lots,
    descending_price_at,
    minimum_next_bid,
    place_bid,
    validate_lot_input,
)
from app.utils import eur_to_cents, utcnow
from tests.conftest import make_user

T0 = utcnow().replace(microsecond=0)


# --- чистые функции ----------------------------------------------------------

def test_descending_price_steps_down_and_stops_at_floor():
    args = dict(start_cents=10000, floor_cents=5000, step_cents=1000, interval_seconds=60, opens_at=T0)
    assert descending_price_at(now=T0 - timedelta(seconds=5), **args) == 10000
    assert descending_price_at(now=T0, **args) == 10000
    assert descending_price_at(now=T0 + timedelta(seconds=59), **args) == 10000
    assert descending_price_at(now=T0 + timedelta(seconds=60), **args) == 9000
    assert descending_price_at(now=T0 + timedelta(seconds=119), **args) == 9000
    assert descending_price_at(now=T0 + timedelta(seconds=600), **args) == 5000


def test_minimum_next_bid():
    assert minimum_next_bid(start_cents=1000, current_cents=1000, step_cents=100, has_bids=False) == 1000
    assert minimum_next_bid(start_cents=1000, current_cents=1500, step_cents=100, has_bids=True) == 1600


@pytest.mark.parametrize("raw,expected", [("12,50", 1250), ("12.5", 1250), ("0", 0), (" 3 ", 300)])
def test_eur_to_cents_accepts_common_formats(raw, expected):
    assert eur_to_cents(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "-1", "", "NaN"])
def test_eur_to_cents_rejects_garbage(raw):
    with pytest.raises(ValueError):
        eur_to_cents(raw)


def test_validate_lot_requires_floor_and_interval_for_descending():
    base = {"title": "Lamp", "mode": "descending", "start_price_cents": 10000, "step_cents": 500,
            "opens_at": T0, "ends_at": T0 + timedelta(hours=1)}

    errors = validate_lot_input(base)

    assert "floor_price" in errors
    assert "drop_interval" in errors


def test_validate_lot_accepts_ascending_without_floor():
    data = {"title": "Lamp", "mode": "ascending", "start_price_cents": 1000, "step_cents": 100,
            "opens_at": T0, "ends_at": T0 + timedelta(hours=1)}

    assert validate_lot_input(data) == []


# --- движок в базе -----------------------------------------------------------

def _lot(app, **overrides):
    with app.app_context():
        values = dict(title="Lot", description="", mode="ascending", start_price_cents=1000,
                      step_cents=100, current_price_cents=1000, status="open",
                      opens_at=T0 - timedelta(minutes=1), ends_at=T0 + timedelta(hours=1))
        values.update(overrides)
        lot = Lot(**values)
        db.session.add(lot)
        db.session.commit()
        return lot.id


def test_ascending_rejects_bid_below_minimum_and_accepts_next(app):
    lot_id = _lot(app)
    first_id = make_user(app, email="a@example.com", verified=True)
    second_id = make_user(app, email="b@example.com", verified=True)
    with app.app_context():
        first = db.session.get(User, first_id)
        second = db.session.get(User, second_id)

        with pytest.raises(AuctionError) as low:
            place_bid(lot_id, first, 900, now=T0)
        assert low.value.code == "bid_too_low"

        place_bid(lot_id, first, 1000, now=T0)
        db.session.commit()
        with pytest.raises(AuctionError):
            place_bid(lot_id, second, 1050, now=T0)  # меньше текущей + шаг
        db.session.rollback()
        place_bid(lot_id, second, 1100, now=T0)
        db.session.commit()

        lot = db.session.get(Lot, lot_id)
        assert lot.current_price_cents == 1100
        assert db.session.query(Bid).filter_by(lot_id=lot_id).count() == 2


def test_ascending_winner_is_last_bidder_after_end(app):
    lot_id = _lot(app)
    user_id = make_user(app, email="w@example.com", verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        place_bid(lot_id, user, 1200, now=T0)
        db.session.commit()

        closed = close_due_lots(now=T0 + timedelta(hours=2))
        db.session.commit()

        lot = db.session.get(Lot, lot_id)
        assert closed == 1
        assert lot.status == "closed"
        assert lot.winner_id == user_id
        assert lot.winning_price_cents == 1200


def test_descending_first_buyer_wins_at_current_price(app):
    lot_id = _lot(app, mode="descending", start_price_cents=10000, floor_price_cents=5000,
                  step_cents=1000, drop_interval_seconds=60, current_price_cents=10000,
                  opens_at=T0 - timedelta(minutes=2))
    first_id = make_user(app, email="x@example.com", verified=True)
    second_id = make_user(app, email="y@example.com", verified=True)
    with app.app_context():
        first = db.session.get(User, first_id)
        second = db.session.get(User, second_id)

        # Прошло 2 минуты: цена 8000. Покупатель видел 9000 — платит текущие 8000
        place_bid(lot_id, first, 9000, now=T0)
        db.session.commit()
        lot = db.session.get(Lot, lot_id)
        assert lot.status == "closed"
        assert lot.winning_price_cents == 8000
        assert lot.winner_id == first_id

        with pytest.raises(AuctionError) as late:
            place_bid(lot_id, second, 8000, now=T0)
        assert late.value.code == "closed"


def test_descending_rejects_offer_below_current_price(app):
    lot_id = _lot(app, mode="descending", start_price_cents=10000, floor_price_cents=5000,
                  step_cents=1000, drop_interval_seconds=60, current_price_cents=10000,
                  opens_at=T0)
    user_id = make_user(app, email="z@example.com", verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        with pytest.raises(AuctionError) as too_low:
            place_bid(lot_id, user, 9999, now=T0)
        assert too_low.value.code == "price_too_low"


def test_bid_before_start_is_refused(app):
    lot_id = _lot(app, opens_at=T0 + timedelta(hours=1), ends_at=T0 + timedelta(hours=2))
    user_id = make_user(app, email="s@example.com", verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        with pytest.raises(AuctionError) as early:
            place_bid(lot_id, user, 1000, now=T0)
        assert early.value.code == "not_started"


def test_descending_lot_without_buyer_closes_unsold(app):
    lot_id = _lot(app, mode="descending", start_price_cents=10000, floor_price_cents=5000,
                  step_cents=1000, drop_interval_seconds=60, current_price_cents=10000,
                  opens_at=T0 - timedelta(hours=2), ends_at=T0 - timedelta(minutes=1))
    with app.app_context():
        close_due_lots(now=T0)
        db.session.commit()
        lot = db.session.get(Lot, lot_id)
        assert lot.status == "closed"
        assert lot.winner_id is None
