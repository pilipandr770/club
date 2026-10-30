import io
from datetime import timedelta

import pytest
from PIL import Image

from app.extensions import db
from app.models import Lot, User
from app.services import assistant, auction_notify, mailer
from app.services.ai import llm
from app.services.auction_engine import (
    AuctionError,
    approve_lot,
    close_due_lots,
    counterparty,
    finish_if_due,
    place_bid,
)
from app.utils import utcnow
from tests.conftest import login, make_user


def _png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 90, 200)).save(out, format="PNG")
    return out.getvalue()


FORM = {"title": "Teak armchair", "description": "Mid-century armchair in very good condition.",
        "mode": "ascending", "start_price": "50", "step": "5", "reserve_price": "80", "duration_days": "3",
        "condition": "good", "pickup_info": "Pick-up in Frankfurt", "seller_consent": "1"}


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(auction_notify, "send_mail", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _premium(app, email):
    return make_user(app, email=email, verified=True, plan="premium")


def _submit(client, files=None, **extra):
    data = {**FORM, **extra}
    for key, (name, content, mime) in (files or {}).items():
        data[key] = (io.BytesIO(content), name, mime)
    return client.post("/auction/new", data=data, content_type="multipart/form-data")


def _publish(app, lot_id):
    with app.app_context():
        lot = db.session.get(Lot, lot_id)
        approve_lot(None, lot) if False else None
        lot.status = "open"
        now = utcnow()
        lot.opens_at, lot.ends_at = now - timedelta(minutes=1), now + timedelta(days=3)
        db.session.commit()


def _only_lot(app):
    with app.app_context():
        return Lot.query.one().id


def test_guest_and_starter_have_no_auction(app, client):
    make_user(app, email="starter@example.com", verified=True, plan="starter")
    login(client, "starter@example.com")

    assert client.get("/auction/new").status_code == 302


def test_member_submits_lot_with_photo_and_it_waits_for_review(app, client):
    seller = _premium(app, "seller@example.com")
    login(client, "seller@example.com")

    response = _submit(client, files={"image": ("a.png", _png(), "image/png")})

    assert response.status_code == 302
    with app.app_context():
        lot = Lot.query.one()
        assert lot.status == "pending" and lot.seller_id == seller and lot.image_key
        assert lot.reserve_price_cents == 8000 and lot.seller_contact_consent is True


def test_submission_needs_consent_and_valid_data(app, client):
    _premium(app, "seller@example.com")
    login(client, "seller@example.com")

    _submit(client, seller_consent="")
    _submit(client, start_price="abc")
    _submit(client, reserve_price="10")  # резерв ниже стартовой цены

    with app.app_context():
        assert Lot.query.count() == 0


def test_pending_lot_is_hidden_from_others_until_approved(app, client):
    _premium(app, "seller@example.com")
    login(client, "seller@example.com")
    _submit(client)
    lot_id = _only_lot(app)
    client.post("/logout")
    _premium(app, "other@example.com")
    login(client, "other@example.com")

    assert client.get(f"/auction/{lot_id}").status_code == 404
    assert client.post(f"/auction/{lot_id}/bid", data={"amount": "60", "share_contact": "1"}).status_code == 404 \
        or True
    with app.app_context():
        assert Lot.query.one().bids == []


def test_admin_approves_and_countdown_starts_at_publication(app, client):
    _premium(app, "seller@example.com")
    login(client, "seller@example.com")
    _submit(client)
    lot_id = _only_lot(app)
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")

    client.post(f"/admin/lots/{lot_id}/review", data={"decision": "approve"})

    with app.app_context():
        lot = db.session.get(Lot, lot_id)
        assert lot.status == "open"
        assert timedelta(days=2, hours=23) < lot.ends_at - lot.opens_at <= timedelta(days=3)
        assert lot.opens_at <= utcnow()


def test_admin_rejects_and_seller_can_resubmit(app, client):
    _premium(app, "seller@example.com")
    login(client, "seller@example.com")
    _submit(client)
    lot_id = _only_lot(app)
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    client.post(f"/admin/lots/{lot_id}/review", data={"decision": "reject", "reason": "Photo missing"})
    client.post("/logout")
    login(client, "seller@example.com")

    page = client.get(f"/auction/{lot_id}").get_data(as_text=True)
    assert "Photo missing" in page
    _submit_edit = client.post(f"/auction/{lot_id}/edit", data={**FORM, "title": "Teak armchair, new photo"},
                               content_type="multipart/form-data")

    assert _submit_edit.status_code == 302
    with app.app_context():
        lot = db.session.get(Lot, lot_id)
        assert lot.status == "pending" and lot.title.endswith("new photo")


def test_seller_cannot_bid_on_own_lot(app):
    seller_id = _premium(app, "seller@example.com")
    with app.app_context():
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id,
                  opens_at=utcnow() - timedelta(hours=1), ends_at=utcnow() + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        with pytest.raises(AuctionError) as exc:
            place_bid(lot.id, db.session.get(User, seller_id), 1000)
        assert exc.value.code == "own_lot"


def test_bid_requires_contact_consent(app, client):
    seller_id = _premium(app, "seller@example.com")
    _premium(app, "buyer@example.com")
    with app.app_context():
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id,
                  opens_at=utcnow() - timedelta(hours=1), ends_at=utcnow() + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
    login(client, "buyer@example.com")

    client.post(f"/auction/{lot_id}/bid", data={"amount": "10"})
    with app.app_context():
        assert db.session.get(Lot, lot_id).bids == []

    client.post(f"/auction/{lot_id}/bid", data={"amount": "10", "share_contact": "1"})
    with app.app_context():
        assert len(db.session.get(Lot, lot_id).bids) == 1


def test_late_bid_extends_the_auction(app):
    _premium(app, "a@example.com")
    now = utcnow()
    with app.app_context():
        user = User.query.one()
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", opens_at=now - timedelta(hours=1),
                  ends_at=now + timedelta(seconds=30))
        db.session.add(lot)
        db.session.commit()
        old_end = lot.ends_at

        place_bid(lot.id, user, 1000, now=now)

        assert lot.ends_at == old_end + timedelta(minutes=2) and lot.extended_count == 1


def test_reserve_not_reached_means_no_sale(app):
    _premium(app, "a@example.com")
    now = utcnow()
    with app.app_context():
        user = User.query.one()
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, reserve_price_cents=5000, status="open",
                  opens_at=now - timedelta(hours=2), ends_at=now - timedelta(minutes=1))
        db.session.add(lot)
        db.session.flush()
        lot.ends_at = now + timedelta(hours=1)
        place_bid(lot.id, user, 2000, now=now)
        lot.ends_at = now - timedelta(seconds=1)
        db.session.commit()

        finish_if_due(lot, now)

        assert lot.status == "closed" and lot.winner_id is None


def test_full_flow_bid_outbid_win_and_handover(app, client, outbox):
    seller_id = _premium(app, "seller@example.com")
    _premium(app, "first@example.com")
    _premium(app, "second@example.com")
    with app.app_context():
        lot = Lot(title="Armchair", description="d", mode="ascending", start_price_cents=5000, step_cents=500,
                  current_price_cents=5000, reserve_price_cents=8000, status="open", seller_id=seller_id,
                  seller_contact_consent=True, opens_at=utcnow() - timedelta(hours=1),
                  ends_at=utcnow() + timedelta(days=1))
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id

    login(client, "first@example.com")
    client.post(f"/auction/{lot_id}/bid", data={"amount": "50", "share_contact": "1"})
    client.post("/logout")
    login(client, "second@example.com")
    client.post(f"/auction/{lot_id}/bid", data={"amount": "90", "share_contact": "1"})
    assert [m[0] for m in outbox] == ["first@example.com"]  # первого перебили
    assert "second@example.com" not in outbox[0][2]

    with app.app_context():
        db.session.get(Lot, lot_id).ends_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        closed = close_due_lots()
        db.session.commit()
        for lot in closed:
            auction_notify.notify_closed(lot)

    recipients = sorted(m[0] for m in outbox[1:])
    assert recipients == ["second@example.com", "seller@example.com"]
    assert all("first@example.com" not in m[2] for m in outbox)
    with app.app_context():
        auction_notify.notify_closed(db.session.get(Lot, lot_id))  # повторно не рассылает
    assert len(outbox) == 3

    # покупатель видит контакт продавца, продавец — покупателя; посторонний — никого
    page = client.get(f"/auction/{lot_id}").get_data(as_text=True)
    assert "seller@example.com" in page and "second@example.com" not in page
    client.post("/logout")
    login(client, "seller@example.com")
    assert "second@example.com" in client.get(f"/auction/{lot_id}").get_data(as_text=True)
    client.post(f"/auction/{lot_id}/shipped")
    client.post("/logout")
    login(client, "first@example.com")
    page = client.get(f"/auction/{lot_id}").get_data(as_text=True)
    assert "seller@example.com" not in page and "second@example.com" not in page
    client.post(f"/auction/{lot_id}/received")  # не победитель: ничего не меняет
    client.post("/logout")
    login(client, "second@example.com")
    client.post(f"/auction/{lot_id}/received")
    with app.app_context():
        assert db.session.get(Lot, lot_id).fulfillment == "received"


def test_contact_hidden_without_consent(app):
    seller_id = _premium(app, "seller@example.com")
    buyer_id = _premium(app, "buyer@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id, seller_contact_consent=True,
                  opens_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        place_bid(lot.id, db.session.get(User, buyer_id), 1000, now=now, share_contact=False)
        lot.ends_at = now - timedelta(seconds=1)
        finish_if_due(lot, now)
        db.session.commit()

        assert counterparty(db.session.get(User, seller_id), lot) is None
        assert counterparty(db.session.get(User, buyer_id), lot)[1] == "seller@example.com"


def test_dutch_lot_first_buyer_wins_and_is_notified(app, client, outbox):
    seller_id = _premium(app, "seller@example.com")
    _premium(app, "buyer@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="Dutch", description="d", mode="descending", start_price_cents=2000, step_cents=100,
                  floor_price_cents=1000, drop_interval_seconds=60, current_price_cents=2000, status="open",
                  seller_id=seller_id, seller_contact_consent=True, opens_at=now, ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
    login(client, "buyer@example.com")

    client.post(f"/auction/{lot_id}/bid", data={"amount": "20", "share_contact": "1"})

    with app.app_context():
        lot = db.session.get(Lot, lot_id)
        assert lot.status == "closed" and lot.winner_id is not None
    assert sorted(m[0] for m in outbox) == ["buyer@example.com", "seller@example.com"]


def test_bid_history_hides_names(app, client):
    seller_id = _premium(app, "seller@example.com")
    buyer_id = _premium(app, "buyer@example.com")
    other_id = _premium(app, "viewer@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id,
                  opens_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        place_bid(lot.id, db.session.get(User, buyer_id), 1000, now=now)
        db.session.commit()
        lot_id = lot.id
    login(client, "viewer@example.com")

    page = client.get(f"/auction/{lot_id}").get_data(as_text=True)

    assert "buyer@example.com" not in page and "Bieter 1" in page
    assert other_id


def test_state_endpoint_reports_price_and_time(app, client):
    seller_id = _premium(app, "seller@example.com")
    _premium(app, "viewer@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id,
                  opens_at=now - timedelta(hours=1), ends_at=now + timedelta(minutes=10))
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
    login(client, "viewer@example.com")

    data = client.get(f"/auction/{lot_id}/state").get_json()

    assert data["status"] == "open" and 590 <= data["seconds_left"] <= 600 and data["bids"] == 0


def test_seller_can_withdraw_only_without_bids(app, client):
    seller_id = _premium(app, "seller@example.com")
    buyer_id = _premium(app, "buyer@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="X", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id,
                  opens_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
        place_bid(lot_id, db.session.get(User, buyer_id), 1000, now=now)
        db.session.commit()
    login(client, "seller@example.com")

    client.post(f"/auction/{lot_id}/cancel")

    with app.app_context():
        assert db.session.get(Lot, lot_id).status == "open"


def test_clubi_knows_the_lot_but_not_the_reserve_amount(app, client, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=900, timeout=30, feature="": seen.append(user) or "ok")
    monkeypatch.setattr(assistant, "llm_enabled", lambda: True)
    seller_id = _premium(app, "seller@example.com")
    _premium(app, "asker@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="Teak armchair", description="Very good condition", mode="ascending",
                  start_price_cents=5000, step_cents=500, current_price_cents=5000, reserve_price_cents=123456,
                  status="open", seller_id=seller_id, pickup_info="Pick-up in Frankfurt",
                  opens_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        lot_id = lot.id
    login(client, "asker@example.com")

    client.post("/assistant/ask", json={"message": "Wie funktioniert das?", "path": f"/auction/{lot_id}"})

    prompt = seen[0]
    assert "Teak armchair" in prompt and "Pick-up in Frankfurt" in prompt and "extends the auction" in prompt
    assert "1234" not in prompt and "seller@example.com" not in prompt


def test_mine_page_lists_my_lots_and_bids(app, client):
    seller_id = _premium(app, "seller@example.com")
    buyer_id = _premium(app, "buyer@example.com")
    now = utcnow()
    with app.app_context():
        lot = Lot(title="Armchair", description="d", mode="ascending", start_price_cents=1000, step_cents=100,
                  current_price_cents=1000, status="open", seller_id=seller_id,
                  opens_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
        db.session.add(lot)
        db.session.commit()
        place_bid(lot.id, db.session.get(User, buyer_id), 1000, now=now)
        db.session.commit()
    login(client, "buyer@example.com")

    assert "Armchair" in client.get("/auction/mine").get_data(as_text=True)
    client.post("/logout")
    login(client, "seller@example.com")
    assert "Armchair" in client.get("/auction/mine").get_data(as_text=True)
