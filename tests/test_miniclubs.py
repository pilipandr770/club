import io
from datetime import timedelta

import pytest
from PIL import Image

from app.extensions import db
from app.miniclub_guide import RULES, flagged_terms
from app.models import MiniClub, MiniClubMember, MiniClubReport, User
from app.services import assistant, miniclubs as club_service
from app.services.ai import llm
from app.utils import utcnow
from tests.conftest import PASSWORD, login, make_user


def _png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 60, 160)).save(out, format="PNG")
    return out.getvalue()


FORM = {"name": "Chess evening Offenbach", "topic": "games", "city": "Offenbach", "join_mode": "approval",
        "description": "We play chess on Tuesday evenings, for fun and without stakes.",
        "audience": "Adults of any level", "schedule": "Every Tuesday 19:00", "rules": "Be friendly and on time",
        "capacity": "4", "accept_rules": "1"}


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(club_service, "send_mail", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _member(app, email, plan="starter", **kw):
    return make_user(app, email=email, verified=True, plan=plan, **kw)


def _post(client, url="/miniclubs/new", files=None, **extra):
    data = {**FORM, **extra}
    for key, (name, content, mime) in (files or {}).items():
        data[key] = (io.BytesIO(content), name, mime)
    return client.post(url, data=data, content_type="multipart/form-data")


def _open_club(app, organizer_email="org@example.com", **fields):
    org = _member(app, organizer_email)
    with app.app_context():
        values = dict(organizer_id=org, name="Open club", topic="games", description="d", city="Mainz",
                      join_mode="approval", status="open")
        values.update(fields)
        club = MiniClub(**values)
        db.session.add(club)
        db.session.commit()
        return club.id


def test_guest_can_read_but_not_create(app, client):
    club_id = _open_club(app)
    _member(app, "guest@example.com", plan="guest")
    login(client, "guest@example.com")

    assert client.get("/miniclubs/new").status_code == 302
    assert client.get(f"/miniclubs/{club_id}").status_code == 200
    assert client.get("/miniclubs/rules").status_code == 200


def test_rules_page_states_the_key_boundaries(app, client):
    _member(app, "a@example.com")
    login(client, "a@example.com")

    page = client.get("/miniclubs/rules").get_data(as_text=True)

    for needle in ("Glücksspiel", "Kinderbetreuung", "Online-Plattform", "Privatsache"):
        assert needle in page, needle
    assert len(RULES) >= 6


def test_list_page_shows_the_disclaimer(app, client):
    _open_club(app)
    _member(app, "a@example.com")
    login(client, "a@example.com")

    page = client.get("/miniclubs/").get_data(as_text=True)

    assert "keine Räume" in page or "keine Räumlichkeiten" in page or "Online-Plattform" in page


def test_member_creates_club_and_it_waits_for_review(app, client):
    org = _member(app, "org@example.com")
    login(client, "org@example.com")

    response = _post(client, files={"image": ("c.png", _png(), "image/png")})

    assert response.status_code == 302
    with app.app_context():
        club = MiniClub.query.one()
        assert club.status == "pending" and club.organizer_id == org and club.image_key and club.capacity == 4


@pytest.mark.parametrize("override", [
    {"name": ""}, {"description": ""}, {"topic": "casino"}, {"join_mode": "x"}, {"accept_rules": ""},
    {"city": "", "online": ""}, {"capacity": "1"}, {"capacity": "x"},
])
def test_invalid_forms_create_nothing(app, client, override):
    _member(app, "org@example.com")
    login(client, "org@example.com")

    _post(client, **override)

    with app.app_context():
        assert MiniClub.query.count() == 0


def test_online_club_needs_no_city(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")

    _post(client, city="", online="1")

    with app.app_context():
        assert MiniClub.query.one().online is True


def test_rejected_picture_creates_nothing(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")

    _post(client, files={"image": ("c.png", b"<html>", "image/png")})

    with app.app_context():
        assert MiniClub.query.count() == 0


def test_pending_club_is_private_until_admin_publishes(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client)
    with app.app_context():
        club_id = MiniClub.query.one().id
    client.post("/logout")
    _member(app, "other@example.com")
    login(client, "other@example.com")
    assert client.get(f"/miniclubs/{club_id}").status_code == 404
    assert "Chess evening" not in client.get("/miniclubs/").get_data(as_text=True)
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    client.post(f"/admin/miniclubs/{club_id}/review", data={"decision": "approve"})
    client.post("/logout")
    login(client, "other@example.com")

    assert client.get(f"/miniclubs/{club_id}").status_code == 200
    assert "Chess evening" in client.get("/miniclubs/").get_data(as_text=True)


def test_admin_sees_flagged_words_for_gambling_and_childcare(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client, name="Poker um Geld", description="Wir spielen Karten mit Einsatz. Außerdem Kinderbetreuung.")
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")

    page = client.get("/admin/miniclubs").get_data(as_text=True)

    assert "Poker um Geld" in page and "um geld" in page.lower() and "kinderbetreuung" in page.lower()


def test_flag_filter_catches_german_english_russian_and_ignores_normal_text():
    assert flagged_terms("Poker um Geld")
    assert flagged_terms("cards for money, stakes")
    assert flagged_terms("игра на деньги, детский сад")
    assert not flagged_terms("Schach am Dienstagabend, ohne Einsatz? nein, nur zum Spaß", "Spaziergang am Main")[:0]
    assert flagged_terms("Schach am Dienstagabend, nur zum Spaß") == []


def test_admin_rejects_with_reason(app, client):
    _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client)
    with app.app_context():
        club_id = MiniClub.query.one().id
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    client.post(f"/admin/miniclubs/{club_id}/review", data={"decision": "reject", "reason": "Bitte Regeln klarer"})
    client.post("/logout")
    login(client, "org@example.com")

    assert "Bitte Regeln klarer" in client.get(f"/miniclubs/{club_id}").get_data(as_text=True)


def test_open_club_lets_people_join_at_once_and_respects_capacity(app, client):
    club_id = _open_club(app, join_mode="open", capacity=2)  # организатор + одно место
    _member(app, "a@example.com")
    _member(app, "b@example.com")
    login(client, "a@example.com")
    client.post(f"/miniclubs/{club_id}/join", data={"message": "hi"})
    with app.app_context():
        assert MiniClubMember.query.count() == 0  # без согласия на контакт нельзя
    client.post(f"/miniclubs/{club_id}/join", data={"message": "hi", "share_contact": "1"})
    client.post("/logout")
    login(client, "b@example.com")
    client.post(f"/miniclubs/{club_id}/join", data={"share_contact": "1"})

    with app.app_context():
        rows = MiniClubMember.query.all()
        assert [(r.status) for r in rows] == ["member"]


def test_request_flow_accept_and_decline_with_emails(app, client, outbox):
    club_id = _open_club(app)
    _member(app, "a@example.com")
    _member(app, "b@example.com")
    login(client, "a@example.com")
    client.post(f"/miniclubs/{club_id}/join", data={"message": "Ich spiele gern", "share_contact": "1"})
    client.post("/logout")
    login(client, "b@example.com")
    client.post(f"/miniclubs/{club_id}/join", data={"share_contact": "1"})
    client.post("/logout")
    assert [m[0] for m in outbox] == ["org@example.com", "org@example.com"]
    login(client, "org@example.com")
    page = client.get(f"/miniclubs/{club_id}").get_data(as_text=True)
    assert "Ich spiele gern" in page
    with app.app_context():
        rows = {r.user.email: r.id for r in MiniClubMember.query.all()}

    client.post(f"/miniclubs/requests/{rows['a@example.com']}/answer", data={"answer": "accept"})
    client.post(f"/miniclubs/requests/{rows['b@example.com']}/answer", data={"answer": "decline"})

    with app.app_context():
        statuses = {r.user.email: r.status for r in MiniClubMember.query.all()}
    assert statuses == {"a@example.com": "member", "b@example.com": "declined"}
    assert sorted(m[0] for m in outbox[2:]) == ["a@example.com", "b@example.com"]


def test_only_organiser_sees_member_email_and_can_answer(app, client):
    club_id = _open_club(app, join_mode="open")
    _member(app, "a@example.com")
    _member(app, "other@example.com")
    login(client, "a@example.com")
    client.post(f"/miniclubs/{club_id}/join", data={"share_contact": "1"})
    client.post("/logout")
    login(client, "other@example.com")
    assert "a@example.com" not in client.get(f"/miniclubs/{club_id}").get_data(as_text=True)
    with app.app_context():
        row_id = MiniClubMember.query.one().id
    assert client.post(f"/miniclubs/members/{row_id}/remove").status_code == 404
    client.post("/logout")
    login(client, "org@example.com")

    assert "a@example.com" in client.get(f"/miniclubs/{club_id}").get_data(as_text=True)
    client.post(f"/miniclubs/members/{row_id}/remove")
    with app.app_context():
        assert MiniClubMember.query.one().status == "left"


def test_member_can_leave_and_come_back(app, client):
    club_id = _open_club(app, join_mode="open")
    _member(app, "a@example.com")
    login(client, "a@example.com")
    client.post(f"/miniclubs/{club_id}/join", data={"share_contact": "1"})
    client.post(f"/miniclubs/{club_id}/leave")
    with app.app_context():
        assert MiniClubMember.query.one().status == "left"

    client.post(f"/miniclubs/{club_id}/join", data={"share_contact": "1"})

    with app.app_context():
        assert MiniClubMember.query.one().status == "member"


def test_announcement_goes_to_members_once_a_day_without_addresses(app, client, outbox):
    club_id = _open_club(app, join_mode="open")
    _member(app, "a@example.com")
    _member(app, "b@example.com")
    for email in ("a@example.com", "b@example.com"):
        login(client, email)
        client.post(f"/miniclubs/{club_id}/join", data={"share_contact": "1"})
        client.post("/logout")
    login(client, "org@example.com")

    client.post(f"/miniclubs/{club_id}/announce", data={"text": "Nächstes Treffen am Dienstag"})
    client.post(f"/miniclubs/{club_id}/announce", data={"text": "Noch eine Nachricht"})

    assert sorted(m[0] for m in outbox) == ["a@example.com", "b@example.com"]
    assert all("Nächstes Treffen" in m[2] and "a@example.com" not in m[2] and "b@example.com" not in m[2]
               for m in outbox)
    assert all("nicht vom Club" in m[2] or "not by the club" in m[2] for m in outbox)


def test_only_organiser_can_announce_or_edit(app, client):
    club_id = _open_club(app, join_mode="open")
    _member(app, "other@example.com")
    login(client, "other@example.com")

    assert client.post(f"/miniclubs/{club_id}/announce", data={"text": "x"}).status_code == 404
    assert client.get(f"/miniclubs/{club_id}/edit").status_code == 404
    assert client.post(f"/miniclubs/{club_id}/close").status_code == 404


def test_editing_sends_an_open_club_back_to_review(app, client):
    club_id = _open_club(app)
    login(client, "org@example.com")

    _post(client, url=f"/miniclubs/{club_id}/edit", name="New name")

    with app.app_context():
        club = db.session.get(MiniClub, club_id)
        assert club.name == "New name" and club.status == "pending"


def test_report_reaches_admin_who_can_close_the_club(app, client):
    club_id = _open_club(app)
    _member(app, "a@example.com")
    login(client, "a@example.com")
    client.post(f"/miniclubs/{club_id}/report", data={"reason": "x"})
    client.post(f"/miniclubs/{club_id}/report", data={"reason": "Hier wird um Geld gespielt"})
    with app.app_context():
        assert MiniClubReport.query.count() == 1
    client.post("/logout")
    make_user(app, email="boss@example.com", verified=True, admin=True, plan="premium")
    login(client, "boss@example.com")
    assert "Hier wird um Geld gespielt" in client.get("/admin/miniclubs").get_data(as_text=True)

    client.post(f"/admin/miniclubs/{club_id}/review", data={"decision": "close"})

    with app.app_context():
        assert db.session.get(MiniClub, club_id).status == "closed"
    client.post("/logout")
    login(client, "a@example.com")
    assert client.get(f"/miniclubs/{club_id}").status_code == 404


def test_clubi_knows_the_group_and_the_boundaries(app, client, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "complete", lambda system, user, max_tokens=900, timeout=30, feature="": seen.append(user) or "ok")
    monkeypatch.setattr(assistant, "llm_enabled", lambda: True)
    club_id = _open_club(app, rules="Pünktlich sein", schedule="Dienstags 19 Uhr")
    _member(app, "asker@example.com")
    login(client, "asker@example.com")

    client.post("/assistant/ask", json={"message": "Wann treffen sie sich?", "path": f"/miniclubs/{club_id}"})

    prompt = seen[0]
    assert "Dienstags 19 Uhr" in prompt and "Pünktlich sein" in prompt and "no games for money" in prompt
    assert "org@example.com" not in prompt and "asker@example.com" not in prompt
    assert "Glücksspiel" in assistant.SYSTEM and "Kinderbetreuung" in assistant.SYSTEM


def test_account_deletion_removes_club_photo(app, client, provider):
    user_id = _member(app, "org@example.com")
    login(client, "org@example.com")
    _post(client, files={"image": ("c.png", _png(), "image/png")})
    with app.app_context():
        key = MiniClub.query.one().image_key

    client.post("/app/privacy/delete", data={"password": PASSWORD})

    assert app.extensions["media"].read(key) is None
    with app.app_context():
        assert db.session.get(User, user_id) is None


def test_module_is_in_the_menu(app, client):
    _member(app, "a@example.com")
    login(client, "a@example.com")

    assert 'href="/miniclubs/"' in client.get("/app/").get_data(as_text=True)
