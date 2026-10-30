import io
from datetime import date, timedelta

from PIL import Image

from app.extensions import db
from app.models import Booking, Trip, User
from app.services import assistant
from app.services.ai import llm
from tests.conftest import PASSWORD, login, make_user
from tests.test_startup_pitch import _pdf

TODAY = date.today()
FORM = {
    "title": "Lisbon week", "destination": "Lisbon", "starts_on": (TODAY + timedelta(days=30)).isoformat(),
    "ends_on": (TODAY + timedelta(days=37)).isoformat(), "seats_total": "4", "price": "450",
    "description": "Food and walks", "program": "Day 1: arrival and welcome dinner",
    "included": "Six nights", "meeting_point": "Hotel lobby at 10:00",
}


def _png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 80, 20)).save(out, format="PNG")
    return out.getvalue()


def _post(client, url="/travel/new", files=None, **extra):
    data = {**FORM, **extra}
    for key, (name, content, mime) in (files or {}).items():
        data[key] = (io.BytesIO(content), name, mime)
    return client.post(url, data=data, content_type="multipart/form-data")


def _trip_id(app):
    with app.app_context():
        return Trip.query.one().id


def test_member_creates_trip_with_photo_and_programme(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")

    response = _post(client, files={"cover": ("c.png", _png(), "image/png"),
                                    "program": ("p.pdf", _pdf("Programme day one at the harbour"), "application/pdf")})

    assert response.status_code == 302
    with app.app_context():
        trip = Trip.query.one()
        assert trip.status == "open" and trip.cover_key and trip.program_key
        assert "harbour" in trip.program_text and trip.meeting_point == "Hotel lobby at 10:00"


def test_bad_files_create_nothing(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")

    _post(client, files={"program": ("p.pdf", b"nope", "application/pdf")})
    _post(client, files={"cover": ("c.png", b"<html>", "image/png")})

    with app.app_context():
        assert Trip.query.count() == 0


def test_cover_and_programme_follow_trip_visibility(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client, files={"cover": ("c.png", _png(), "image/png"), "program": ("p.pdf", _pdf(), "application/pdf")})
    tid = _trip_id(app)
    client.post("/logout")
    make_user(app, email="guest@example.com", verified=True, plan="guest")
    login(client, "guest@example.com")

    program = client.get(f"/travel/{tid}/program")

    assert client.get(f"/travel/{tid}/cover").mimetype == "image/png"
    assert program.mimetype == "application/pdf" and program.headers["Content-Disposition"].startswith("attachment")
    with app.app_context():
        trip = db.session.get(Trip, tid)
        trip.status = "cancelled"
        db.session.commit()
    assert client.get(f"/travel/{tid}/cover").status_code == 404
    assert client.get(f"/travel/{tid}/program").status_code == 404


def test_booking_needs_explicit_contact_consent(app, client):
    org = make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client)
    tid = _trip_id(app)
    client.post("/logout")
    make_user(app, email="joiner@example.com", verified=True, plan="starter")
    login(client, "joiner@example.com")

    client.post(f"/travel/{tid}/join", data={"seats": "1"})
    with app.app_context():
        assert Booking.query.count() == 0

    client.post(f"/travel/{tid}/join", data={"seats": "1", "share_contact": "1"})
    with app.app_context():
        assert Booking.query.one().share_contact is True
    assert org


def test_only_organiser_sees_participant_email(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client)
    tid = _trip_id(app)
    client.post("/logout")
    make_user(app, email="joiner@example.com", verified=True, plan="starter")
    login(client, "joiner@example.com")
    client.post(f"/travel/{tid}/join", data={"seats": "1", "share_contact": "1"})
    assert "joiner@example.com" not in client.get(f"/travel/{tid}").get_data(as_text=True)
    client.post("/logout")

    login(client, "org@example.com")

    assert "joiner@example.com" in client.get(f"/travel/{tid}").get_data(as_text=True)


def test_organiser_can_edit_but_not_below_booked_seats(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client, seats_total="4")
    tid = _trip_id(app)
    client.post("/logout")
    make_user(app, email="joiner@example.com", verified=True, plan="starter")
    login(client, "joiner@example.com")
    client.post(f"/travel/{tid}/join", data={"seats": "3", "share_contact": "1"})
    client.post("/logout")
    login(client, "org@example.com")

    _post(client, url=f"/travel/{tid}/edit", seats_total="2", title="Too small")
    _post(client, url=f"/travel/{tid}/edit", seats_total="6", title="Lisbon extended")

    with app.app_context():
        trip = db.session.get(Trip, tid)
        assert trip.title == "Lisbon extended" and trip.seats_total == 6


def test_other_member_cannot_edit_trip(app, client):
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client)
    tid = _trip_id(app)
    client.post("/logout")
    make_user(app, email="other@example.com", verified=True, plan="starter")
    login(client, "other@example.com")

    assert client.get(f"/travel/{tid}/edit").status_code == 404
    assert _post(client, url=f"/travel/{tid}/edit", title="Hijacked").status_code == 404


def test_clubi_sees_whole_trip_card_but_no_emails(app, client, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=900, timeout=30, feature="": seen.append(user) or "ok")
    monkeypatch.setattr(assistant, "llm_enabled", lambda: True)
    make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client, files={"program": ("p.pdf", _pdf("Boat tour on Tuesday"), "application/pdf")})
    tid = _trip_id(app)
    client.post("/logout")
    make_user(app, email="asker@example.com", verified=True, plan="guest")
    login(client, "asker@example.com")

    client.post("/assistant/ask", json={"message": "Wo treffen wir uns?", "path": f"/travel/{tid}"})

    prompt = seen[0]
    assert "Hotel lobby at 10:00" in prompt and "Boat tour on Tuesday" in prompt and "Six nights" in prompt
    assert "org@example.com" not in prompt and "asker@example.com" not in prompt


def test_account_deletion_removes_trip_files(app, client, provider):
    user_id = make_user(app, email="org@example.com", verified=True, plan="starter")
    login(client, "org@example.com")
    _post(client, files={"cover": ("c.png", _png(), "image/png"), "program": ("p.pdf", _pdf(), "application/pdf")})
    with app.app_context():
        trip = Trip.query.one()
        keys = [trip.cover_key, trip.program_key]

    client.post("/app/privacy/delete", data={"password": PASSWORD})

    for key in keys:
        assert app.extensions["media"].read(key) is None
    with app.app_context():
        assert db.session.get(User, user_id) is None
