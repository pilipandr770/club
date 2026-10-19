from datetime import date

from app.extensions import db
from app.models import WebhookEvent
from app.models.user import User
from app.services.entitlements import has_feature
from app.services.identity import IdentityResult, is_adult
from tests.conftest import login, make_user, post_webhook, stripe_event


def _start(client):
    return client.post("/app/verify")


def _reload(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        return {
            "status": user.verification_status,
            "reason": user.verification_reason,
            "attempts": user.verification_attempts,
            "verified": user.verified_at is not None,
            "session": user.stripe_verification_session_id,
        }


# --- возраст -----------------------------------------------------------------

def test_is_adult_on_eighteenth_birthday():
    dob = {"day": 19, "month": 10, "year": 2008}
    assert is_adult(dob, date(2026, 10, 19)) is True
    assert is_adult(dob, date(2026, 10, 18)) is False


def test_is_adult_leap_day_birthday():
    dob = {"day": 29, "month": 2, "year": 2008}
    assert is_adult(dob, date(2026, 3, 1)) is True
    assert is_adult(dob, date(2026, 2, 28)) is False


def test_is_adult_without_date_of_birth_is_unknown():
    assert is_adult(None, date(2026, 1, 1)) is None
    assert is_adult({"day": "x"}, date(2026, 1, 1)) is None


# --- старт проверки ----------------------------------------------------------

def test_unconfirmed_email_cannot_start_verification(app, client, provider):
    user_id = make_user(app, confirmed=False)
    login(client)

    response = _start(client)

    assert response.status_code == 302
    assert provider.created == []
    assert _reload(app, user_id)["status"] == "none"


def test_confirmed_user_gets_redirect_to_provider(app, client, provider):
    user_id = make_user(app)
    login(client)

    response = _start(client)

    assert response.status_code == 303
    assert response.headers["Location"] == "https://verify.example/vs_test_1"
    assert provider.created[0][0] == user_id
    state = _reload(app, user_id)
    assert state["status"] == "pending"
    assert state["session"] == "vs_test_1"


def test_already_verified_user_cannot_start_again(app, client, provider):
    make_user(app, verified=True)
    login(client)

    _start(client)

    assert provider.created == []


# --- вебхук ------------------------------------------------------------------

def test_webhook_with_bad_signature_changes_nothing(app, client, provider):
    user_id = make_user(app)
    payload = stripe_event("verified", "vs_test_1", user_id)

    response = client.post("/webhooks/stripe", data=payload, content_type="application/json",
                           headers={"Stripe-Signature": "forged"})

    assert response.status_code == 400
    assert _reload(app, user_id)["status"] == "none"


def test_verified_adult_from_allowed_country_is_verified(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)

    response = post_webhook(client, stripe_event("verified", "vs_test_1", user_id))

    assert response.status_code == 200
    state = _reload(app, user_id)
    assert state["status"] == "verified"
    assert state["verified"] is True
    assert state["reason"] is None


def test_underage_is_refused_without_retries(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)
    provider.result = IdentityResult(status="verified", document_country="DE", is_adult=False)

    post_webhook(client, stripe_event("verified", "vs_test_1", user_id))

    state = _reload(app, user_id)
    assert state["status"] == "failed"
    assert state["reason"] == "underage"
    assert state["attempts"] == 0


def test_sanctioned_document_country_is_refused(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)
    provider.result = IdentityResult(status="verified", document_country="RU", is_adult=True)

    post_webhook(client, stripe_event("verified", "vs_test_1", user_id))

    state = _reload(app, user_id)
    assert state["status"] == "failed"
    assert state["reason"] == "sanctioned_country"


def test_failed_user_cannot_start_a_new_session(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)
    provider.result = IdentityResult(status="verified", document_country="RU", is_adult=True)
    post_webhook(client, stripe_event("verified", "vs_test_1", user_id))

    _start(client)

    assert len(provider.created) == 1
    assert _reload(app, user_id)["status"] == "failed"


def test_unknown_country_goes_to_manual_review(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)
    provider.result = IdentityResult(status="verified", document_country=None, is_adult=True)

    post_webhook(client, stripe_event("verified", "vs_test_1", user_id))

    state = _reload(app, user_id)
    assert state["status"] == "manual"
    assert state["reason"] == "needs_review"


def test_canceled_attempts_lead_to_manual_after_limit(app, client, provider):
    user_id = make_user(app)
    login(client)
    for index in range(3):
        _start(client)
        post_webhook(client, stripe_event("canceled", f"vs_test_{index + 1}", user_id,
                                          event_id=f"evt_cancel_{index}"))

    state = _reload(app, user_id)
    assert state["attempts"] == 3
    assert state["status"] == "manual"


def test_duplicate_event_is_processed_once(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)
    payload = stripe_event("canceled", "vs_test_1", user_id, event_id="evt_same")

    post_webhook(client, payload)
    response = post_webhook(client, payload)

    assert response.status_code == 200
    assert _reload(app, user_id)["attempts"] == 1
    with app.app_context():
        assert WebhookEvent.query.count() == 1


def test_stale_session_event_is_ignored(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)

    post_webhook(client, stripe_event("canceled", "vs_old_session", user_id, event_id="evt_stale"))

    assert _reload(app, user_id)["attempts"] == 0


def test_provider_failure_returns_500_and_does_not_record_event(app, client, provider):
    user_id = make_user(app)
    login(client)
    _start(client)
    provider.fail_fetch = True

    response = post_webhook(client, stripe_event("verified", "vs_test_1", user_id, event_id="evt_retry"))

    assert response.status_code == 500
    with app.app_context():
        assert db.session.get(WebhookEvent, "evt_retry") is None
    assert _reload(app, user_id)["status"] == "pending"


# --- права гостя -------------------------------------------------------------

def test_guest_gets_limited_features_only_after_verification(app):
    verified_id = make_user(app, verified=True)
    unverified_id = make_user(app, email="new@example.com")
    with app.app_context():
        verified = db.session.get(User, verified_id)
        unverified = db.session.get(User, unverified_id)
        assert has_feature(verified, "dashboard") is True
        assert has_feature(verified, "admin.users") is False
        assert has_feature(unverified, "dashboard") is False
