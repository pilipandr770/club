import io
from datetime import timedelta

import pytest
from PIL import Image

from app.extensions import db
from app.models import Event, EventInvite, EventRsvp, Profile, User
from app.services import assistant, events as event_service
from app.services.ai import llm
from app.utils import utc_to_local_input, utcnow
from tests.conftest import PASSWORD, login, make_user


def _png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 160, 90)).save(out, format="PNG")
    return out.getvalue()


def _local(delta_hours):
    return utc_to_local_input(utcnow() + timedelta(hours=delta_hours))


FORM = {"title": "Founders evening", "event_type": "meetup", "event_format": "in_person",
        "description": "Short talks and dinner for founders.", "audience": "Founders and freelancers",
        "agenda": "18:30 arrival, 19:00 talks", "requirements": "Bring a laptop", "language": "German",
        "venue": "Cafe Main", "address": "Hauptstrasse 1, Frankfurt", "capacity": "3", "price": "",
        "starts_at": _local(48), "ends_at": _local(51)}


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(event_service, "send_mail", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _member(app, email, plan="starter", **kw):
    return make_user(app, email=email, verified=True, plan=plan, **kw)


def _post(client, url="/events/new", files=None, **extra):
    data = {**FORM, **extra}
    for key, (name, content, mime) in (files or {}).items():
        data[key] = (io.BytesIO(content), name, mime)
    return client.post(url, data=data, content_type="multipart/form-data")


def _make_open(app, organizer_email="org@example.com", **fields):
    org = _member(app, organizer_email)
    with app.app_context():
        values = dict(organizer_id=org, title="Open event", event_type="meetup", event_format="in_person",
                      description="d", venue="Cafe", starts_at=utcnow() + timedelta(days=2),
                      ends_at=utcnow() + timedelta(days=2, hours=3), status="open")
        values.update(fields)
        event = Event(**values)
        db.session.add(event)
        db.session.commit()
        return event.id


def test_guest_cannot_create_but_can_read(app, client):
    event_id = _make_open(app)
    _member(app, "guest@example.com", plan="guest")
    login(client, "guest@example.com")

    assert client.get("/events/new").status_code == 302
    assert client.get(f"/events/{event_id}").status_code == 200


def test_member_creates_meeting_with_photo_and_it_waits_for_review(app, client):
    org = _member(app, "org@example.com")
    login(client, "org@example.com")

    response = _post(client, files={"image": ("e.png", _png(), "image/png")})

    assert response.status_code == 302
    with app.app_context():
        event = Event.query.one()
        assert event.status == "pending" and event.organizer_id == org and event.image_key
        assert event.capacity == 3 and event.venue == "Cafe Main"


@pytest.mark.parametrize("override", [
    {"title": ""}, {"description": ""}, {"event_format": "teleport"}, {"venue": ""},
    {"starts_at": _local(-5), "ends_at": _local(-2)}, {"ends_at": _local(40)}, {"capacity": "0"},
    {"event_format": "online", "online_url": ""}, {"online_url": "javascript:alert(1)", "event_format": "online"},
])
def test_invalid_forms_create_nothing(app, client, override):
    _member(app, "org@example.com")
    login(client, "org@example.com")

    _post(client, **override)

    with app.app_context():
        assert Event.query.count() == 0


def test_bad_photo_creates_nothing(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")

    _post(client, files={"image": ("e.png", b"<html>", "image/png")})

    with app.app_context():
        assert Event.query.count() == 0


def test_pending_event_is_private_until_admin_publishes(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client, files={"image": ("e.png", _png(), "image/png")})
    with app.app_context():
        event_id = Event.query.one().id
    client.post("/logout")
    _member(app, "other@example.com")
    login(client, "other@example.com")
    assert client.get(f"/events/{event_id}").status_code == 404
    assert client.get(f"/events/{event_id}/image").status_code == 404
    assert "Founders evening" not in client.get("/events/").get_data(as_text=True)
    client.post("/logout")

    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    assert client.get(f"/events/{event_id}").status_code == 200
    client.post(f"/admin/events/{event_id}/review", data={"decision": "approve"})
    client.post("/logout")
    login(client, "other@example.com")

    assert client.get(f"/events/{event_id}").status_code == 200
    assert client.get(f"/events/{event_id}/image").mimetype == "image/png"
    assert "Founders evening" in client.get("/events/").get_data(as_text=True)


def test_admin_rejects_with_reason_and_organiser_sees_it(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client)
    with app.app_context():
        event_id = Event.query.one().id
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    client.post(f"/admin/events/{event_id}/review", data={"decision": "reject", "reason": "Venue unclear"})
    client.post("/logout")
    login(client, "org@example.com")

    assert "Venue unclear" in client.get(f"/events/{event_id}").get_data(as_text=True)


def test_admin_created_event_is_published_at_once(app, client):
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")

    _post(client)

    with app.app_context():
        assert Event.query.one().status == "open"


def test_non_admin_cannot_review(app, client):
    event_id = _make_open(app, status="pending")
    _member(app, "other@example.com")
    login(client, "other@example.com")

    assert client.post(f"/admin/events/{event_id}/review", data={"decision": "approve"}).status_code == 404


def test_signup_needs_consent_and_respects_capacity(app, client):
    event_id = _make_open(app, capacity=3)
    _member(app, "a@example.com")
    _member(app, "b@example.com")
    login(client, "a@example.com")

    client.post(f"/events/{event_id}/join", data={"guests": "2"})
    with app.app_context():
        assert EventRsvp.query.count() == 0
    client.post(f"/events/{event_id}/join", data={"guests": "2", "share_contact": "1"})
    client.post("/logout")
    login(client, "b@example.com")
    client.post(f"/events/{event_id}/join", data={"guests": "2", "share_contact": "1"})  # осталось одно место

    with app.app_context():
        assert EventRsvp.query.count() == 1
        assert event_service.seats_left(db.session.get(Event, event_id)) == 1


def test_organiser_cannot_join_own_event_and_leave_frees_places(app, client):
    event_id = _make_open(app, capacity=2)
    _member(app, "a@example.com")
    login(client, "org@example.com") if False else None
    login(client, "a@example.com")
    client.post(f"/events/{event_id}/join", data={"guests": "2", "share_contact": "1"})
    with app.app_context():
        assert event_service.seats_left(db.session.get(Event, event_id)) == 0
    client.post(f"/events/{event_id}/leave")

    with app.app_context():
        assert event_service.seats_left(db.session.get(Event, event_id)) == 2
        organiser = User.query.filter_by(email="org@example.com").one()
        with pytest.raises(event_service.EventError):
            event_service.join(organiser, event_id, 1, True)


def test_only_organiser_sees_guest_email_and_online_link_needs_signup(app, client):
    event_id = _make_open(app, event_format="online", online_url="https://meet.example/room")
    _member(app, "guest@example.com")
    login(client, "guest@example.com")
    assert "meet.example" not in client.get(f"/events/{event_id}").get_data(as_text=True)
    client.post(f"/events/{event_id}/join", data={"guests": "1", "share_contact": "1"})
    page = client.get(f"/events/{event_id}").get_data(as_text=True)
    assert "meet.example/room" in page and "guest@example.com" not in page
    client.post("/logout")

    login(client, "org@example.com")

    assert "guest@example.com" in client.get(f"/events/{event_id}").get_data(as_text=True)


def test_invitations_go_to_directory_members_without_revealing_addresses(app, client, outbox):
    event_id = _make_open(app)
    visible = _member(app, "visible@example.com")
    hidden = _member(app, "hidden@example.com")
    with app.app_context():
        db.session.get(User, visible).profile.visible_in_directory = True
        db.session.commit()
    login(client, "org@example.com")

    page = client.get(f"/events/{event_id}").get_data(as_text=True)
    assert "visible@example.com" not in page and f'value="{visible}"' in page and f'value="{hidden}"' not in page

    client.post(f"/events/{event_id}/invite", data={"members": [str(visible), str(hidden)]})

    with app.app_context():
        assert [i.user_id for i in EventInvite.query.all()] == [visible]
    assert [m[0] for m in outbox] == ["visible@example.com"]
    client.post("/logout")
    login(client, "visible@example.com")
    assert "Open event" in client.get("/events/").get_data(as_text=True)


def test_only_organiser_can_invite_or_edit(app, client):
    event_id = _make_open(app)
    _member(app, "other@example.com")
    login(client, "other@example.com")

    assert client.post(f"/events/{event_id}/invite", data={"members": ["1"]}).status_code == 404
    assert client.get(f"/events/{event_id}/edit").status_code == 404
    assert _post(client, url=f"/events/{event_id}/edit", title="Hijacked").status_code == 404


def test_editing_an_open_event_sends_it_back_to_review(app, client):
    event_id = _make_open(app)
    login(client, "org@example.com")

    _post(client, url=f"/events/{event_id}/edit", title="New title")

    with app.app_context():
        event = db.session.get(Event, event_id)
        assert event.title == "New title" and event.status == "pending"


def test_capacity_cannot_drop_below_signups(app, client):
    event_id = _make_open(app, capacity=5)
    _member(app, "a@example.com")
    login(client, "a@example.com")
    client.post(f"/events/{event_id}/join", data={"guests": "3", "share_contact": "1"})
    client.post("/logout")
    login(client, "org@example.com")

    _post(client, url=f"/events/{event_id}/edit", capacity="2", title="Changed")

    with app.app_context():
        assert db.session.get(Event, event_id).title == "Open event"


def test_cancelling_informs_everyone_who_signed_up(app, client, outbox):
    event_id = _make_open(app)
    _member(app, "a@example.com")
    login(client, "a@example.com")
    client.post(f"/events/{event_id}/join", data={"guests": "1", "share_contact": "1"})
    client.post("/logout")
    login(client, "org@example.com")

    client.post(f"/events/{event_id}/cancel")

    with app.app_context():
        assert db.session.get(Event, event_id).status == "cancelled"
    assert [m[0] for m in outbox] == ["a@example.com"]
    client.post("/logout")
    login(client, "a@example.com")
    assert client.get(f"/events/{event_id}").status_code == 200  # записавшийся видит отмену


def test_calendar_file_has_no_online_link_and_correct_times(app, client):
    event_id = _make_open(app, event_format="online", online_url="https://meet.example/room")
    _member(app, "a@example.com")
    login(client, "a@example.com")

    response = client.get(f"/events/{event_id}/calendar.ics")

    body = response.get_data(as_text=True)
    assert response.mimetype == "text/calendar" and "BEGIN:VEVENT" in body and "SUMMARY:Open event" in body
    assert "meet.example" not in body


def test_clubi_knows_the_meeting_but_no_emails(app, client, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=900, timeout=30, feature="": seen.append(user) or "ok")
    monkeypatch.setattr(assistant, "llm_enabled", lambda: True)
    event_id = _make_open(app, agenda="19:00 two short talks", audience="Founders", venue="Cafe Main")
    _member(app, "asker@example.com")
    login(client, "asker@example.com")

    client.post("/assistant/ask", json={"message": "Wer sollte kommen?", "path": f"/events/{event_id}"})

    prompt = seen[0]
    assert "19:00 two short talks" in prompt and "Founders" in prompt and "Cafe Main" in prompt
    assert "org@example.com" not in prompt and "asker@example.com" not in prompt


def test_account_deletion_removes_event_photo(app, client, provider):
    user_id = _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client, files={"image": ("e.png", _png(), "image/png")})
    with app.app_context():
        key = Event.query.one().image_key

    client.post("/app/privacy/delete", data={"password": PASSWORD})

    assert app.extensions["media"].read(key) is None
    with app.app_context():
        assert db.session.get(User, user_id) is None


def test_events_module_is_in_the_menu(app, client):
    _member(app, "a@example.com")
    login(client, "a@example.com")

    assert 'href="/events/"' in client.get("/app/").get_data(as_text=True)
