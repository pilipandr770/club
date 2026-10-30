"""Подписки Stripe: оформление, вебхук, состояние тарифа, удаление аккаунта."""

import json
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import AuditLog, User, WebhookEvent
from app.services import billing
from app.services.billing import BillingError, SubscriptionInfo
from app.utils import utcnow
from tests.conftest import PASSWORD, login, make_user, post_webhook


class FakeBilling:
    """Заменяет Stripe Billing: ни одного сетевого вызова."""

    def __init__(self):
        self.customers = []
        self.checkouts = []
        self.portals = []
        self.cancelled = []
        self.configured = True
        self.subscription = None

    def price_id(self, plan, interval):
        if not self.configured:
            raise BillingError("not_configured")
        return f"price_{plan}_{interval}"

    def create_customer(self, user):
        self.customers.append(user.id)
        return f"cus_{user.id}"

    def checkout_url(self, customer_id, user, price_id, success_url, cancel_url, terms_text):
        self.checkouts.append((customer_id, price_id, success_url, terms_text))
        return f"https://checkout.example/{price_id}"

    def portal_url(self, customer_id, return_url):
        self.portals.append(customer_id)
        return "https://portal.example/session"

    def fetch_subscription(self, subscription_id):
        return self.subscription

    def cancel_subscription(self, subscription_id):
        self.cancelled.append(subscription_id)


@pytest.fixture()
def fake_billing(app, provider):
    fake = FakeBilling()
    original = app.extensions["billing"]
    app.extensions["billing"] = fake
    yield fake
    app.extensions["billing"] = original


def _info(user_id, plan="growth", interval="month", status="active", days=30, cancels=False, sub="sub_1"):
    return SubscriptionInfo(id=sub, customer_id=f"cus_{user_id}", status=status, plan=plan, interval=interval,
                            period_end=utcnow() + timedelta(days=days), cancel_at_period_end=cancels, user_id=user_id)


def _event(event_id="evt_sub_1", event_type="customer.subscription.updated", sub="sub_1"):
    return json.dumps({"id": event_id, "type": event_type, "data": {"object": {"id": sub}}})


def test_page_is_for_logged_in_members_and_shows_the_prices(app, client, fake_billing):
    assert client.get("/app/plan").status_code == 302
    make_user(app, verified=True)
    login(client)
    page = client.get("/app/plan").get_data(as_text=True)
    assert "19,00 €" in page and "49,00 €" in page and "99,00 €" in page
    assert page.count('name="interval"') == 6


def test_unverified_member_sees_no_purchase_buttons(app, client, fake_billing):
    make_user(app)
    login(client)
    page = client.get("/app/plan").get_data(as_text=True)
    assert 'name="interval"' not in page
    assert client.post("/app/plan/checkout", data={"plan": "starter", "interval": "month"}).status_code == 302
    assert fake_billing.checkouts == []


def test_checkout_creates_customer_once_and_redirects_to_stripe(app, client, fake_billing):
    uid = make_user(app, verified=True)
    login(client)
    response = client.post("/app/plan/checkout", data={"plan": "growth", "interval": "year"})
    assert response.status_code == 303 and response.headers["Location"] == "https://checkout.example/price_growth_year"
    client.post("/app/plan/checkout", data={"plan": "starter", "interval": "month"})
    assert fake_billing.customers == [uid]
    with app.app_context():
        assert db.session.get(User, uid).stripe_customer_id == f"cus_{uid}"
        assert "billing.checkout_started" in {row.action for row in AuditLog.query.all()}
    assert "/nutzungsbedingungen" in fake_billing.checkouts[0][3]


@pytest.mark.parametrize("data", [{"plan": "guest", "interval": "month"}, {"plan": "premium", "interval": "week"}, {}])
def test_checkout_rejects_unknown_choices(app, client, fake_billing, data):
    make_user(app, verified=True)
    login(client)
    assert client.post("/app/plan/checkout", data=data).status_code == 302
    assert fake_billing.checkouts == []


def test_checkout_without_configured_prices_shows_a_message_not_an_error(app, client, fake_billing):
    make_user(app, verified=True)
    login(client)
    fake_billing.configured = False
    response = client.post("/app/plan/checkout", data={"plan": "starter", "interval": "month"}, follow_redirects=True)
    assert response.status_code == 200 and fake_billing.checkouts == []


def test_subscription_event_sets_plan_and_paid_until_with_grace(app, client, fake_billing):
    uid = make_user(app, verified=True)
    fake_billing.subscription = _info(uid, "growth", days=30)
    assert post_webhook(client, _event()).status_code == 200
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.plan == "growth" and user.effective_plan == "growth"
        assert user.subscription_status == "active" and user.stripe_subscription_id == "sub_1"
        assert timedelta(days=32) < user.paid_until - utcnow() < timedelta(days=34)


def test_duplicate_event_is_ignored(app, client, fake_billing):
    uid = make_user(app, verified=True)
    fake_billing.subscription = _info(uid)
    post_webhook(client, _event())
    fake_billing.subscription = _info(uid, plan="premium")
    assert post_webhook(client, _event()).status_code == 200
    with app.app_context():
        assert db.session.get(User, uid).plan == "growth"
        assert WebhookEvent.query.count() == 1


def test_cancelled_subscription_keeps_the_paid_period_then_expires(app, client, fake_billing):
    uid = make_user(app, verified=True)
    fake_billing.subscription = _info(uid, days=10)
    post_webhook(client, _event("evt_a"))
    fake_billing.subscription = _info(uid, status="canceled", days=10)
    post_webhook(client, _event("evt_b", "customer.subscription.deleted"))
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.subscription_status == "canceled" and user.effective_plan == "growth"
        user.paid_until = utcnow() - timedelta(days=1)
        db.session.commit()
        assert user.effective_plan == "guest"
        assert not billing.has_subscription(user)


def test_past_due_keeps_the_plan_and_the_page_warns(app, client, fake_billing):
    uid = make_user(app, verified=True)
    fake_billing.subscription = _info(uid, status="past_due")
    post_webhook(client, _event())
    login(client)
    page = client.get("/app/plan").get_data(as_text=True)
    assert "Zahlung" in page or "Payment failed" in page
    with app.app_context():
        assert db.session.get(User, uid).effective_plan == "growth"


def test_event_for_unknown_user_is_acknowledged(app, client, fake_billing):
    fake_billing.subscription = _info(99999)
    assert post_webhook(client, _event()).status_code == 200


def test_provider_failure_returns_500_so_stripe_retries(app, client, fake_billing):
    make_user(app, verified=True)

    def boom(_id):
        raise RuntimeError("stripe down")

    fake_billing.fetch_subscription = boom
    assert post_webhook(client, _event()).status_code == 500
    with app.app_context():
        assert WebhookEvent.query.count() == 0


def test_manual_plan_without_end_date_is_not_replaced_by_a_subscription(app, client, fake_billing):
    uid = make_user(app, verified=True, plan="premium")
    fake_billing.subscription = _info(uid, "starter")
    post_webhook(client, _event())
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.plan == "premium" and user.paid_until is None and user.subscription_status == "active"


def test_longer_manual_period_of_the_same_plan_is_kept(app, client, fake_billing):
    uid = make_user(app, verified=True, plan="growth")
    far = utcnow() + timedelta(days=200)
    with app.app_context():
        user = db.session.get(User, uid)
        user.paid_until = far
        db.session.commit()
    fake_billing.subscription = _info(uid, "growth", days=30)
    post_webhook(client, _event())
    with app.app_context():
        assert abs((db.session.get(User, uid).paid_until - far).total_seconds()) < 1


def test_subscribed_member_gets_the_portal_instead_of_a_second_subscription(app, client, fake_billing):
    uid = make_user(app, verified=True)
    fake_billing.subscription = _info(uid)
    post_webhook(client, _event())
    login(client)
    page = client.get("/app/plan").get_data(as_text=True)
    assert 'name="interval"' not in page
    client.post("/app/plan/checkout", data={"plan": "premium", "interval": "month"})
    assert fake_billing.checkouts == []
    response = client.post("/app/plan/portal")
    assert response.status_code == 303 and fake_billing.portals == [f"cus_{uid}"]


def test_portal_without_a_customer_is_refused(app, client, fake_billing):
    make_user(app, verified=True)
    login(client)
    assert client.post("/app/plan/portal").status_code == 302 and fake_billing.portals == []


def test_deleting_the_account_cancels_the_subscription(app, client, fake_billing, provider):
    uid = make_user(app, verified=True)
    fake_billing.subscription = _info(uid)
    post_webhook(client, _event())
    login(client)
    client.post("/app/privacy/delete", data={"password": PASSWORD})
    assert fake_billing.cancelled == ["sub_1"]
    with app.app_context():
        assert db.session.get(User, uid) is None


def test_prices_are_consistent():
    from config.plans import PAID_PLANS, PLANS, price_cents

    for plan in PAID_PLANS:
        assert price_cents(plan, "year") < price_cents(plan, "month") * 12
        assert PLANS[plan]["price_eur_cents"] > 0
