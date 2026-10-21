import json

import pytest

from app import create_app
from app.extensions import db
from app.models import User
from app.services.identity import IdentityResult

PASSWORD = "correct-horse-battery"


class FakeIdentityProvider:
    """Заменяет Stripe в тестах: ни одного сетевого вызова."""

    def __init__(self):
        self.created = []
        self.redacted = []
        self.result = IdentityResult(status="verified", document_country="DE", is_adult=True)
        self.fail_fetch = False

    def create_session(self, user_id, return_url):
        session_id = f"vs_test_{len(self.created) + 1}"
        self.created.append((user_id, session_id, return_url))
        return session_id, f"https://verify.example/{session_id}"

    def construct_event(self, payload, signature):
        if signature != "good-signature":
            raise ValueError("invalid signature")
        return json.loads(payload)

    def fetch_result(self, session_id):
        if self.fail_fetch:
            raise RuntimeError("provider unavailable")
        return self.result

    def redact(self, session_id):
        self.redacted.append(session_id)


@pytest.fixture(scope="session")
def app():
    # Контекст открываем только на время создания схемы: иначе запросы клиента
    # переиспользуют одну сессию SQLAlchemy на все тесты.
    application = create_app("test")
    with application.app_context():
        db.drop_all()
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS vector"))  # векторы корпуса законов (pgvector)
        db.session.commit()
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def provider(app):
    fake = FakeIdentityProvider()
    original = app.extensions["identity_provider"]
    app.extensions["identity_provider"] = fake
    yield fake
    app.extensions["identity_provider"] = original


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Каждый тест начинается с пустых таблиц."""
    yield
    with app.app_context():
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


def make_user(app, email="member@example.com", verified=False, confirmed=True,
              residence="DE", status="active", plan="guest", admin=False):
    """Создаёт участника напрямую в БД и возвращает его id."""
    from app.models import Profile
    from app.utils import utcnow

    with app.app_context():
        user = User(email=email, first_name="Test", last_name="Member", status=status, plan=plan,
                    is_admin=admin)
        user.set_password(PASSWORD)
        user.profile = Profile(residence_country=residence)
        if confirmed:
            user.email_confirmed_at = utcnow()
        if verified:
            user.verification_status = "verified"
            user.verified_at = utcnow()
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, email="member@example.com"):
    return client.post("/login", data={"email": email, "password": PASSWORD})


def stripe_event(event_type, session_id, user_id, event_id="evt_1"):
    return json.dumps({
        "id": event_id,
        "type": f"identity.verification_session.{event_type}",
        "data": {"object": {"id": session_id, "metadata": {"user_id": str(user_id)}}},
    })


def post_webhook(client, payload):
    return client.post(
        "/webhooks/stripe",
        data=payload,
        content_type="application/json",
        headers={"Stripe-Signature": "good-signature"},
    )
