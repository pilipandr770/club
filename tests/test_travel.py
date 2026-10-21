from datetime import date, timedelta

from app.extensions import db
from app.models import Booking, Trip, User
from app.services.travel import TravelError, join, parse_trip_form
from tests.conftest import login, make_user

TODAY = date.today()
FORM = {
    "title": "Lisbon week", "destination": "Lisbon",
    "starts_on": (TODAY + timedelta(days=30)).isoformat(),
    "ends_on": (TODAY + timedelta(days=37)).isoformat(),
    "seats_total": "4", "price": "450", "description": "Food and walks",
}


class Form(dict):
    def get(self, key, default=""):
        return dict.get(self, key, default)


def _trip(app, author_id, kind="shared", status="open", seats=4, starts_in=30, ends_in=37):
    with app.app_context():
        trip = Trip(author_id=author_id, kind=kind, status=status, title="Trip", description="",
                    destination="Porto", starts_on=TODAY + timedelta(days=starts_in),
                    ends_on=TODAY + timedelta(days=ends_in), seats_total=seats)
        db.session.add(trip)
        db.session.commit()
        return trip.id


def _set_role(app, user_id, role):
    with app.app_context():
        db.session.get(User, user_id).role = role
        db.session.commit()


# --- разбор формы ------------------------------------------------------------

def test_form_accepts_valid_trip():
    data, errors = parse_trip_form(Form(FORM), today=TODAY)

    assert errors == []
    assert data["seats_total"] == 4
    assert data["price_per_person_cents"] == 45000


def test_form_refuses_past_dates_reversed_dates_and_zero_seats():
    past = dict(FORM, starts_on=(TODAY - timedelta(days=1)).isoformat())
    reversed_ = dict(FORM, ends_on=(TODAY + timedelta(days=1)).isoformat())
    no_seats = dict(FORM, seats_total="0")

    assert "dates" in parse_trip_form(Form(past), today=TODAY)[1]
    assert "dates" in parse_trip_form(Form(reversed_), today=TODAY)[1]
    assert "seats_total" in parse_trip_form(Form(no_seats), today=TODAY)[1]


# --- совместные поездки ------------------------------------------------------

def test_member_opens_shared_trip_and_guest_can_read_it(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    make_user(app, email="guest@example.com", verified=True, plan="guest")
    login(client, "org@example.com")

    client.post("/travel/new", data=dict(FORM, csrf_token="x"))

    with app.app_context():
        trip = Trip.query.one()
        assert trip.kind == "shared" and trip.status == "open"
    client.get("/logout")
    login(client, "guest@example.com")
    assert "Lisbon week" in client.get("/travel/").get_data(as_text=True)


def test_guest_cannot_join(app, client):
    org = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org)
    make_user(app, email="guest@example.com", verified=True, plan="guest")
    login(client, "guest@example.com")

    client.post(f"/travel/{trip_id}/join", data={"seats": "1", "share_contact": "1"})

    with app.app_context():
        assert Booking.query.count() == 0


def test_starter_joins_and_seats_drop(app, client):
    org = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org, seats=3)
    make_user(app, email="a@example.com", verified=True, plan="starter")
    login(client, "a@example.com")

    client.post(f"/travel/{trip_id}/join", data={"seats": "2", "share_contact": "1"})

    with app.app_context():
        booking = Booking.query.one()
        assert booking.seats == 2
        assert booking.status == "confirmed"


def test_capacity_is_enforced(app):
    org_id = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org_id, seats=3)
    first_id = make_user(app, email="a@example.com", verified=True, plan="starter")
    second_id = make_user(app, email="b@example.com", verified=True, plan="starter")
    with app.app_context():
        join(db.session.get(User, first_id), trip_id, 2, today=TODAY, share_contact=True)
        db.session.commit()
        try:
            join(db.session.get(User, second_id), trip_id, 2, today=TODAY, share_contact=True)
            raised = None
        except TravelError as exc:
            raised = exc.code
        db.session.rollback()
        assert raised == "not_enough_seats"


def test_double_booking_is_refused_and_leave_frees_seats(app, client):
    org = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org, seats=2)
    make_user(app, email="a@example.com", verified=True, plan="starter")
    login(client, "a@example.com")

    client.post(f"/travel/{trip_id}/join", data={"seats": "1", "share_contact": "1"})
    client.post(f"/travel/{trip_id}/join", data={"seats": "1", "share_contact": "1"})
    with app.app_context():
        assert Booking.query.count() == 1

    client.post(f"/travel/{trip_id}/leave")
    client.post(f"/travel/{trip_id}/join", data={"seats": "2", "share_contact": "1"})
    with app.app_context():
        assert Booking.query.count() == 1  # та же строка: история сохранена
        assert Booking.query.one().seats == 2


def test_organiser_cannot_book_own_trip(app, client):
    org_id = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org_id)
    login(client, "org@example.com")

    client.post(f"/travel/{trip_id}/join", data={"seats": "1", "share_contact": "1"})

    with app.app_context():
        assert Booking.query.count() == 0


def test_participants_are_listed_by_first_name(app, client):
    org_id = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org_id)
    make_user(app, email="a@example.com", verified=True, plan="starter")
    login(client, "a@example.com")
    client.post(f"/travel/{trip_id}/join", data={"seats": "1", "share_contact": "1"})

    body = client.get(f"/travel/{trip_id}").get_data(as_text=True)

    assert "<p>Test</p>" in body  # имя участника из списка, а не случайное совпадение


def test_author_cancels_trip_and_others_cannot(app, client):
    org_id = make_user(app, email="org@example.com", verified=True, plan="starter")
    trip_id = _trip(app, org_id)
    make_user(app, email="other@example.com", verified=True, plan="starter")
    login(client, "other@example.com")
    client.post(f"/travel/{trip_id}/cancel")
    with app.app_context():
        assert db.session.get(Trip, trip_id).status == "open"

    client.post("/logout")
    login(client, "org@example.com")
    client.post(f"/travel/{trip_id}/cancel")
    with app.app_context():
        assert db.session.get(Trip, trip_id).status == "cancelled"


# --- предложения операторов и агентов ---------------------------------------

def test_starter_cannot_publish_offer(app, client):
    org_id = make_user(app, email="op@example.com", verified=True, plan="starter")
    _set_role(app, org_id, "travel_operator")
    login(client, "op@example.com")

    assert client.get("/travel/offers/new").status_code == 302


def test_growth_member_without_operator_role_cannot_publish_offer(app, client):
    make_user(app, email="member@example.com", verified=True, plan="growth")
    login(client, "member@example.com")

    client.post("/travel/offers/new", data=dict(FORM, csrf_token="x"), follow_redirects=True)

    with app.app_context():
        assert Trip.query.filter_by(kind="offer").count() == 0


def test_operator_offer_waits_for_review_then_is_published(app, client):
    op_id = make_user(app, email="op@example.com", verified=True, plan="growth")
    _set_role(app, op_id, "travel_operator")
    login(client, "op@example.com")
    client.post("/travel/offers/new", data=dict(FORM, csrf_token="x"))

    with app.app_context():
        offer = Trip.query.filter_by(kind="offer").one()
        assert offer.status == "pending"
        offer_id = offer.id
    assert "Lisbon week" not in client.get("/travel/").get_data(as_text=True)

    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True)
    login(client, "boss@example.com")
    client.post(f"/admin/travel/{offer_id}/review", data={"decision": "approve"})

    client.post("/logout")
    login(client, "op@example.com")
    assert "Lisbon week" in client.get("/travel/").get_data(as_text=True)


def test_admin_rejects_offer_with_reason(app, client):
    op_id = make_user(app, email="agent@example.com", verified=True, plan="growth")
    _set_role(app, op_id, "travel_agent")
    offer_id = _trip(app, op_id, kind="offer", status="pending")
    make_user(app, email="boss@example.com", verified=True, admin=True)
    login(client, "boss@example.com")

    client.post(f"/admin/travel/{offer_id}/review", data={"decision": "reject", "reason": "No prices"})

    with app.app_context():
        offer = db.session.get(Trip, offer_id)
        assert offer.status == "rejected"
        assert offer.rejection_reason == "No prices"


def test_cannot_book_unapproved_offer(app, client):
    op_id = make_user(app, email="op@example.com", verified=True, plan="growth")
    offer_id = _trip(app, op_id, kind="offer", status="pending")
    make_user(app, email="a@example.com", verified=True, plan="starter")
    login(client, "a@example.com")

    client.post(f"/travel/{offer_id}/join", data={"seats": "1", "share_contact": "1"})

    with app.app_context():
        assert Booking.query.count() == 0
