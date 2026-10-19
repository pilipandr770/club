import json

from app.extensions import db
from app.models import Consent, User
from tests.conftest import PASSWORD, login, make_user


def test_export_contains_account_but_no_documents(app, client):
    make_user(app, verified=True)
    login(client)

    response = client.get("/app/privacy/export")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == 'attachment; filename="club-export.json"'
    data = json.loads(response.get_data(as_text=True))
    assert data["konto"]["email"] == "member@example.com"
    assert data["identitaetspruefung"]["status"] == "verified"
    assert "Ausweisdokumente" in data["identitaetspruefung"]["hinweis"]
    assert "geburtsdatum" not in json.dumps(data).lower()


def test_delete_needs_the_correct_password(app, client, provider):
    user_id = make_user(app)
    login(client)

    client.post("/app/privacy/delete", data={"password": "wrong-password-1"})

    with app.app_context():
        assert db.session.get(User, user_id) is not None
    assert provider.redacted == []


def test_delete_redacts_at_provider_and_removes_account(app, client, provider):
    user_id = make_user(app)
    login(client)
    client.post("/app/verify")

    response = client.post("/app/privacy/delete", data={"password": PASSWORD})

    assert response.status_code == 302
    assert provider.redacted == ["vs_test_1"]
    with app.app_context():
        assert db.session.get(User, user_id) is None
        assert Consent.query.count() == 0


def test_delete_keeps_account_when_provider_fails(app, client, provider):
    user_id = make_user(app)
    login(client)
    client.post("/app/verify")

    def broken_redact(session_id):
        raise RuntimeError("Stripe unreachable")

    provider.redact = broken_redact
    client.post("/app/privacy/delete", data={"password": PASSWORD})

    with app.app_context():
        assert db.session.get(User, user_id) is not None
