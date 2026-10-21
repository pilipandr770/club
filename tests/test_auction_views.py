from datetime import timedelta

from app.extensions import db
from app.models import Bid, Lot
from app.utils import utcnow
from tests.conftest import login, make_user


def _open_lot(app, mode="ascending"):
    now = utcnow()
    with app.app_context():
        lot = Lot(title="Lamp", description="Vintage", mode=mode, start_price_cents=1000,
                  step_cents=100, current_price_cents=1000, status="open",
                  floor_price_cents=500 if mode == "descending" else None,
                  drop_interval_seconds=60 if mode == "descending" else None,
                  opens_at=now - timedelta(minutes=1), ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        return lot.id


def test_guest_cannot_open_auction_lot(app, client):
    lot_id = _open_lot(app)
    make_user(app, verified=True, plan="guest")
    login(client)

    assert client.get(f"/auction/{lot_id}").status_code == 302


def test_premium_member_sees_list_and_detail(app, client):
    lot_id = _open_lot(app)
    make_user(app, verified=True, plan="premium")
    login(client)

    assert client.get("/auction/").status_code == 200
    body = client.get(f"/auction/{lot_id}").get_data(as_text=True)
    assert "Lamp" in body
    assert "Gebot abgeben" in body


def test_premium_member_places_ascending_bid_through_form(app, client):
    lot_id = _open_lot(app)
    make_user(app, verified=True, plan="premium")
    login(client)

    response = client.post(f"/auction/{lot_id}/bid", data={"amount": "12,50", "share_contact": "1"})

    assert response.status_code == 302
    with app.app_context():
        assert Bid.query.filter_by(lot_id=lot_id).count() == 1
        assert db.session.get(Lot, lot_id).current_price_cents == 1250


def test_too_low_bid_shows_message_and_changes_nothing(app, client):
    lot_id = _open_lot(app)
    make_user(app, verified=True, plan="premium")
    login(client)

    response = client.post(f"/auction/{lot_id}/bid", data={"amount": "5", "share_contact": "1"}, follow_redirects=True)

    assert "Dein Gebot liegt unter dem Mindestbetrag. Bitte erhöhe es." in response.get_data(as_text=True)
    with app.app_context():
        assert Bid.query.count() == 0


def test_descending_buy_button_takes_current_price(app, client):
    lot_id = _open_lot(app, mode="descending")
    make_user(app, verified=True, plan="premium")
    login(client)

    body = client.get(f"/auction/{lot_id}").get_data(as_text=True)
    # Лот открыт минуту назад, шаг 100 центов за минуту: текущая цена 9,00 €
    assert 'name="amount" value="9.00"' in body

    client.post(f"/auction/{lot_id}/bid", data={"amount": "9.00", "share_contact": "1"})
    with app.app_context():
        lot = db.session.get(Lot, lot_id)
        assert lot.status == "closed"
        assert lot.winning_price_cents == 900
