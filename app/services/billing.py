"""Подписки через Stripe Billing: оформление, управление, состояние.

- Клуб не хранит деньги и карты: оплата и ввод карты у Stripe (Checkout), смена и отмена в Customer Portal.
- Тариф и `paid_until` ведёт только подписанный вебхук: при каждом событии подписку читаем у Stripe заново,
  поэтому порядок доставки событий не важен.
- Цены и продукты создаёт `flask stripe-setup`; находим их по `lookup_key` (club_<тариф>_<период>), ID в коде нет.
- Права проверяем по `User.effective_plan`: после окончания оплаченного срока (плюс короткий запас) — гостевой тариф.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import stripe
from flask import current_app
from flask_babel import gettext as _

from config.plans import INTERVALS, PAID_PLANS

from ..extensions import db
from ..models.user import User
from .audit import audit

log = logging.getLogger(__name__)

SUBSCRIPTION_EVENT_PREFIX = "customer.subscription."
# Статусы, при которых тариф действует. past_due: оплата не прошла, Stripe повторяет попытки, не лишаем доступа сразу.
ACTIVE_STATUSES = ("active", "trialing", "past_due")
GRACE = timedelta(days=3)
PRODUCT_APP = "club"


class BillingError(Exception):
    """Оформление подписки сейчас невозможно. Атрибут code — код для показа участнику."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SubscriptionInfo:
    id: str
    customer_id: str
    status: str  # active | trialing | past_due | canceled | unpaid | incomplete | incomplete_expired | paused
    plan: str | None
    interval: str | None
    period_end: datetime | None  # naive UTC
    cancel_at_period_end: bool
    user_id: int | None


def lookup_key(plan: str, interval: str) -> str:
    return f"{PRODUCT_APP}_{plan}_{interval}"


def _naive_utc(timestamp) -> datetime | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).replace(tzinfo=None)


class StripeBilling:
    """Тонкая обёртка над Stripe Billing. В тестах заменяется фейком с тем же интерфейсом."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _require_key(self):
        if not self.api_key:
            raise RuntimeError("STRIPE_SECRET_KEY не задан в .env")

    def price_id(self, plan: str, interval: str) -> str:
        self._require_key()
        found = stripe.Price.list(api_key=self.api_key, lookup_keys=[lookup_key(plan, interval)], active=True, limit=1)
        if not found["data"]:
            raise BillingError("not_configured")
        return found["data"][0]["id"]

    def create_customer(self, user: User) -> str:
        self._require_key()
        customer = stripe.Customer.create(
            api_key=self.api_key, email=user.email, name=user.full_name,
            preferred_locales=[user.locale or "de"], metadata={"user_id": str(user.id), "app": PRODUCT_APP},
        )
        return customer["id"]

    def checkout_url(self, customer_id: str, user: User, price_id: str, success_url: str, cancel_url: str,
                     terms_text: str) -> str:
        self._require_key()
        session = stripe.checkout.Session.create(
            api_key=self.api_key, mode="subscription", customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=str(user.id),
            subscription_data={"metadata": {"user_id": str(user.id), "app": PRODUCT_APP}},
            success_url=success_url, cancel_url=cancel_url, locale=user.locale or "auto",
            allow_promotion_codes=True, custom_text={"submit": {"message": terms_text}},
        )
        return session["url"]

    def portal_url(self, customer_id: str, return_url: str) -> str:
        self._require_key()
        params = {"api_key": self.api_key, "customer": customer_id, "return_url": return_url}
        configuration = current_app.config.get("STRIPE_PORTAL_CONFIG")
        if configuration:
            params["configuration"] = configuration
        return stripe.billing_portal.Session.create(**params)["url"]

    def fetch_subscription(self, subscription_id: str) -> SubscriptionInfo:
        self._require_key()
        sub = stripe.Subscription.retrieve(subscription_id, api_key=self.api_key, expand=["items.data.price"])
        items = (sub.get("items") or {}).get("data") or []
        price = items[0]["price"] if items else {}
        metadata = price.get("metadata") or {}
        plan = metadata.get("plan")
        interval = (price.get("recurring") or {}).get("interval")
        # Конец оплаченного периода: у новых версий API он лежит на позиции подписки
        period_end = sub.get("current_period_end") or (items[0].get("current_period_end") if items else None)
        user_id = (sub.get("metadata") or {}).get("user_id")
        return SubscriptionInfo(
            id=sub["id"], customer_id=sub["customer"] if isinstance(sub["customer"], str) else sub["customer"]["id"],
            status=sub["status"], plan=plan if plan in PAID_PLANS else None,
            interval=interval if interval in INTERVALS else None, period_end=_naive_utc(period_end),
            cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
            user_id=int(user_id) if user_id and str(user_id).isdigit() else None,
        )

    def cancel_subscription(self, subscription_id: str) -> None:
        self._require_key()
        try:
            stripe.Subscription.cancel(subscription_id, api_key=self.api_key)
        except stripe.error.InvalidRequestError as exc:  # подписка уже завершена
            if getattr(exc, "code", None) != "resource_missing":
                raise


def gateway():
    return current_app.extensions["billing"]


def can_subscribe(user: User) -> bool:
    return user.is_verified and user.status == "active"


def has_subscription(user: User) -> bool:
    return bool(user.stripe_subscription_id) and user.subscription_status in ACTIVE_STATUSES


def start_checkout(user: User, plan: str, interval: str) -> str:
    """Ссылка на оплату в Stripe Checkout. Уже подписанным открываем портал: смена тарифа там, а не вторая подписка."""
    if plan not in PAID_PLANS or interval not in INTERVALS:
        raise BillingError("bad_choice")
    if not can_subscribe(user):
        raise BillingError("not_verified")
    if has_subscription(user):
        raise BillingError("already_subscribed")
    billing = gateway()
    if not user.stripe_customer_id:
        user.stripe_customer_id = billing.create_customer(user)
        db.session.commit()  # клиент уже создан у Stripe: не теряем связь, даже если дальше что-то сорвётся
    base = current_app.config["BASE_URL"]
    terms = _("By subscribing you accept the terms of use: %(url)s", url=f"{base}/nutzungsbedingungen")
    url = billing.checkout_url(
        user.stripe_customer_id, user, billing.price_id(plan, interval),
        success_url=f"{base}/app/plan/return?status=ok", cancel_url=f"{base}/app/plan?status=cancel",
        terms_text=terms,
    )
    audit("billing.checkout_started", target=f"user:{user.id}", actor_id=user.id, details=f"{plan}/{interval}")
    return url


def portal_url(user: User) -> str:
    if not user.stripe_customer_id:
        raise BillingError("no_customer")
    return gateway().portal_url(user.stripe_customer_id, f"{current_app.config['BASE_URL']}/app/plan")


def _find_user(info: SubscriptionInfo) -> User | None:
    user = db.session.get(User, info.user_id) if info.user_id else None
    if user is None:
        user = User.query.filter_by(stripe_customer_id=info.customer_id).first()
    return user


def apply_subscription(user: User, info: SubscriptionInfo) -> str:
    """Переносит состояние подписки на участника. Вызывается из вебхука; возвращает краткий итог для журнала."""
    if user.stripe_subscription_id and user.stripe_subscription_id != info.id:
        # Событие о старой подписке, пока оформлена новая: не трогаем действующую
        if user.subscription_status in ACTIVE_STATUSES and info.status not in ACTIVE_STATUSES:
            return "stale_subscription"
    manual_open_ended = user.plan != "guest" and user.paid_until is None  # ручной тариф без срока подпиской не заменяем
    user.stripe_customer_id = info.customer_id
    user.stripe_subscription_id = info.id
    user.subscription_status = info.status
    user.subscription_interval = info.interval
    user.subscription_cancels = info.cancel_at_period_end

    if info.status in ACTIVE_STATUSES and info.plan and info.period_end and not manual_open_ended:
        until = info.period_end + GRACE
        # Ручная оплата того же тарифа, которая действует дольше, не сокращается
        keep = user.paid_until if user.plan == info.plan and user.paid_until and user.paid_until > until else None
        user.plan = info.plan
        user.paid_until = keep or until
    audit("billing.subscription", target=f"user:{user.id}",
          details=f"{info.status} {info.plan or '-'}/{info.interval or '-'} cancels={info.cancel_at_period_end}")
    return info.status


def handle_subscription_event(event) -> str:
    """Событие customer.subscription.*: читаем подписку у Stripe и применяем. Ошибка пробрасывается: Stripe повторит."""
    obj = event["data"]["object"]
    info = gateway().fetch_subscription(obj["id"])
    user = _find_user(info)
    if user is None:
        return "unknown_user"
    return apply_subscription(user, info)


def cancel_for_deletion(user: User) -> None:
    """При удалении аккаунта подписка завершается сразу, иначе Stripe продолжил бы списывать оплату."""
    if user.stripe_subscription_id and user.subscription_status in ACTIVE_STATUSES:
        gateway().cancel_subscription(user.stripe_subscription_id)


def plan_state(user: User) -> dict:
    """Что показать на странице «Тариф»: источник оплаты и срок."""
    source = "none"
    if has_subscription(user):
        source = "subscription"
    elif user.effective_plan != "guest":
        source = "manual"
    return {
        "source": source,
        "plan": user.effective_plan,
        "until": user.paid_until,
        "cancels": bool(user.subscription_cancels) and source == "subscription",
        "past_due": user.subscription_status == "past_due",
        "interval": user.subscription_interval,
    }
