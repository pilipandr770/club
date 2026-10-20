import pytest

from app.extensions import db
from app.models import IntroRequest, User
from app.services import intros
from app.services.consents import latest_consent
from app.services.intros import IntroError, request_intro, respond, visible_contact
from app.services.matching import refresh_embeddings
from tests.conftest import login, make_user


def _member(app, email, matching=True, verified=True, status="active"):
    user_id = make_user(app, email=email, verified=verified, status=status)
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        profile.q_can_help = "I explain tax deadlines for freelancers."
        profile.q_looking_for = "A designer for a two-week project."
        profile.allow_matching = matching
        refresh_embeddings(profile)
        db.session.commit()
    return user_id


def _user(app, user_id):
    return db.session.get(User, user_id)


# --- согласия ----------------------------------------------------------------

def test_no_decision_means_not_decided(app):
    user_id = make_user(app, verified=True)
    with app.app_context():
        assert latest_consent(_user(app, user_id), "directory") is None


def test_dashboard_asks_for_a_decision_until_made(app, client):
    make_user(app, email="d@example.com", verified=True)
    login(client, "d@example.com")

    assert "Meine Auswahl treffen" in client.get("/app/").get_data(as_text=True)

    client.post("/profile/privacy", data={})
    assert "Meine Auswahl treffen" not in client.get("/app/").get_data(as_text=True)


def test_withdrawing_matching_removes_member_from_candidates(app):
    from app.services.matching import candidates

    me = _member(app, "me@example.com")
    other = _member(app, "other@example.com")
    with app.app_context():
        assert [u.id for u, _p in candidates(_user(app, me))] == [other]
        db.session.get(User, other).profile.allow_matching = False
        db.session.commit()
        assert candidates(_user(app, me)) == []


# --- запросы на контакт ------------------------------------------------------

def test_contact_is_hidden_until_accepted(app):
    a = _member(app, "a@example.com")
    b = _member(app, "b@example.com")
    with app.app_context():
        sender, recipient = _user(app, a), _user(app, b)
        assert visible_contact(sender, recipient) is None
        intro = request_intro(sender, recipient, "Hello")
        db.session.commit()
        assert visible_contact(sender, recipient) is None  # запрос ещё не принят
        respond(recipient, intro, accept=True)
        db.session.commit()
        assert visible_contact(sender, recipient) == "b@example.com"
        assert visible_contact(recipient, sender) == "a@example.com"


def test_declined_request_never_shares_contact(app):
    a = _member(app, "a@example.com")
    b = _member(app, "b@example.com")
    with app.app_context():
        intro = request_intro(_user(app, a), _user(app, b), "Hi")
        db.session.commit()
        respond(_user(app, b), intro, accept=False)
        db.session.commit()
        assert visible_contact(_user(app, a), _user(app, b)) is None


def test_third_member_never_sees_someone_elses_contact(app):
    a = _member(app, "a@example.com")
    b = _member(app, "b@example.com")
    c = _member(app, "c@example.com")
    with app.app_context():
        intro = request_intro(_user(app, a), _user(app, b), "Hi")
        db.session.commit()
        respond(_user(app, b), intro, accept=True)
        db.session.commit()
        assert visible_contact(_user(app, c), _user(app, b)) is None


def test_no_self_requests_no_duplicates_and_no_non_matching_members(app):
    a = _member(app, "a@example.com")
    b = _member(app, "b@example.com")
    closed = _member(app, "closed@example.com", matching=False)
    with app.app_context():
        with pytest.raises(IntroError) as own:
            request_intro(_user(app, a), _user(app, a), "me")
        assert own.value.code == "own_profile"
        with pytest.raises(IntroError) as off:
            request_intro(_user(app, a), _user(app, closed), "hi")
        assert off.value.code == "not_available"
        request_intro(_user(app, a), _user(app, b), "one")
        db.session.commit()
        with pytest.raises(IntroError) as dup:
            request_intro(_user(app, b), _user(app, a), "two")  # в обратную сторону тоже нельзя
        assert dup.value.code == "already_open"


def test_daily_limit_is_enforced(app, monkeypatch):
    monkeypatch.setattr(intros, "INTRO_DAILY_LIMIT", 2)
    a = _member(app, "a@example.com")
    targets = [_member(app, f"t{i}@example.com") for i in range(3)]
    with app.app_context():
        sender = _user(app, a)
        request_intro(sender, _user(app, targets[0]), "1")
        request_intro(sender, _user(app, targets[1]), "2")
        db.session.commit()
        with pytest.raises(IntroError) as limit:
            request_intro(sender, _user(app, targets[2]), "3")
        assert limit.value.code == "daily_limit"


def test_only_recipient_answers_and_only_sender_withdraws(app):
    a = _member(app, "a@example.com")
    b = _member(app, "b@example.com")
    with app.app_context():
        intro = request_intro(_user(app, a), _user(app, b), "Hi")
        db.session.commit()
        with pytest.raises(IntroError):
            respond(_user(app, a), intro, accept=True)
        with pytest.raises(IntroError):
            intros.withdraw(_user(app, b), intro)
        intros.withdraw(_user(app, a), intro)
        db.session.commit()
        assert db.session.get(IntroRequest, intro.id).status == "withdrawn"


def test_accept_is_refused_if_recipient_withdrew_matching(app):
    a = _member(app, "a@example.com")
    b = _member(app, "b@example.com")
    with app.app_context():
        intro = request_intro(_user(app, a), _user(app, b), "Hi")
        db.session.commit()
        db.session.get(User, b).profile.allow_matching = False
        db.session.commit()
        with pytest.raises(IntroError) as blocked:
            respond(_user(app, b), intro, accept=True)
        assert blocked.value.code == "not_available"


# --- страницы ----------------------------------------------------------------

def test_full_flow_through_the_pages(app, client):
    sender_id = _member(app, "sender@example.com")
    recipient_id = _member(app, "recipient@example.com")
    login(client, "sender@example.com")
    assert "Kontakt anfragen" in client.get("/profile/matches").get_data(as_text=True)

    client.post(f"/profile/intro/{recipient_id}", data={"message": "Let us talk"})
    client.post("/logout")

    login(client, "recipient@example.com")
    with app.app_context():
        intro_id = IntroRequest.query.one().id
    assert "sender@example.com" not in client.get("/profile/intros").get_data(as_text=True)
    client.post(f"/profile/intros/{intro_id}/respond", data={"answer": "accept"})
    assert "sender@example.com" in client.get("/profile/intros").get_data(as_text=True)
