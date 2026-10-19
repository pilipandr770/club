from app.extensions import db
from app.models import Consent, Profile, User
from tests.conftest import PASSWORD, login, make_user


def _register_form(**overrides):
    data = {
        "first_name": "Anna",
        "last_name": "Muster",
        "email": "anna@example.com",
        "password": PASSWORD,
        "residence_country": "DE",
        "consent_privacy": "1",
        "consent_terms": "1",
        "consent_identity": "1",
    }
    data.update(overrides)
    return data


def test_register_creates_account_with_consents(app, client):
    response = client.post("/register", data=_register_form())

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/app/")
    with app.app_context():
        user = User.query.filter_by(email="anna@example.com").one()
        assert user.verification_status == "none"
        assert user.plan == "guest"
        assert user.email_confirmed_at is None
        assert user.profile.residence_country == "DE"
        kinds = {c.kind: c.granted for c in Consent.query.filter_by(user_id=user.id)}
        assert kinds == {"privacy": True, "terms": True, "identity": True}


def test_register_refuses_blocked_residence_country(app, client):
    response = client.post("/register", data=_register_form(residence_country="RU"))

    assert response.status_code == 200
    assert "Für Menschen mit Wohnsitz in diesem Land können wir kein Konto eröffnen." in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 0


def test_register_requires_every_consent(app, client):
    data = _register_form()
    del data["consent_identity"]

    response = client.post("/register", data=data)

    assert response.status_code == 200
    assert "Diese Bestätigung brauchen wir" in response.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 0


def test_register_rejects_duplicate_email_and_short_password(app, client):
    make_user(app, email="anna@example.com")

    response = client.post("/register", data=_register_form(password="short"))

    body = response.get_data(as_text=True)
    assert "Mit dieser E-Mail-Adresse gibt es bereits ein Konto." in body
    assert "Das Passwort muss mindestens 10 Zeichen lang sein." in body


def test_login_with_correct_password(app, client):
    make_user(app)

    response = login(client)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/app/")


def test_login_with_wrong_password_is_refused(app, client):
    make_user(app)

    response = client.post("/login", data={"email": "member@example.com", "password": "wrong-password-1"})

    assert response.status_code == 200
    assert "E-Mail oder Passwort sind nicht korrekt." in response.get_data(as_text=True)


def test_blocked_account_cannot_log_in(app, client):
    make_user(app, status="blocked")

    response = login(client)

    assert "Dieses Konto ist gesperrt." in response.get_data(as_text=True)


def test_dashboard_requires_login(client):
    response = client.get("/app/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_logout_requires_post(app, client):
    make_user(app)
    login(client)

    assert client.get("/logout").status_code == 405


def test_email_confirmation_sets_timestamp(app, client):
    from app.services.tokens import make_email_token

    user_id = make_user(app, confirmed=False)
    with app.app_context():
        token = make_email_token(db.session.get(User, user_id))

    response = client.get(f"/confirm/{token}")

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(User, user_id).email_confirmed_at is not None


def test_invalid_confirmation_token_changes_nothing(app, client):
    user_id = make_user(app, confirmed=False)

    response = client.get("/confirm/not-a-real-token")

    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(User, user_id).email_confirmed_at is None


def test_profile_residence_is_stored(app, client):
    make_user(app, residence="AT")
    with app.app_context():
        assert Profile.query.one().residence_country == "AT"
