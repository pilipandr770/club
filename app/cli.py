"""Команды Flask: переводы, администраторы, тестовые участники, закрытие аукционов."""

import os

import click
from babel.messages.frontend import CommandLineInterface
from flask import current_app
from flask.cli import AppGroup

i18n_cli = AppGroup("i18n", help="Переводы (Babel)")

POT = "translations/messages.pot"


def _pybabel(*args):
    CommandLineInterface().run(["pybabel", *args])


@i18n_cli.command("extract")
def extract():
    """Собрать все видимые строки в messages.pot."""
    # _l и lazy_gettext — ленивые строки для моделей и реестров (ARCHITECTURE.md §3)
    _pybabel(
        "extract", "-F", "babel.cfg", "-k", "_l", "-k", "lazy_gettext", "-o", POT, "."
    )


@i18n_cli.command("update")
def update():
    """Обновить каталоги переводов по messages.pot (новые строки появятся как пустые)."""
    _pybabel("update", "-i", POT, "-d", "translations")


@i18n_cli.command("compile")
def compile_():
    """Скомпилировать .po в .mo — без этого перевод не работает."""
    _pybabel("compile", "-d", "translations")
    click.echo("Каталоги скомпилированы.")


@click.command("create-admin")
@click.argument("email")
@click.option("--first-name", default="Admin", show_default=True)
def create_admin(email, first_name):
    """Назначает администратором существующего участника или создаёт нового (пароль спросит интерактивно)."""
    from .extensions import db
    from .models import User
    from .utils import utcnow

    email = email.strip().lower()
    user = User.query.filter_by(email=email).first()
    if user is None:
        password = click.prompt("Пароль (минимум 10 символов)", hide_input=True, confirmation_prompt=True)
        if len(password) < 10:
            raise click.ClickException("Пароль короче 10 символов.")
        user = User(email=email, first_name=first_name, last_name="", plan="premium",
                    email_confirmed_at=utcnow(), verification_status="verified", verified_at=utcnow())
        user.set_password(password)
        user.profile = None
        db.session.add(user)
    user.is_admin = True
    db.session.commit()
    click.echo(f"Администратор: {email}")


@click.command("seed-test-users")
def seed_test_users_command():
    """Создаёт тестовых участников на всех тарифах с одним паролем из TEST_SEED_PASSWORD."""
    from .services.seed import SeedError, seed_test_users

    try:
        rows = seed_test_users(os.environ.get("TEST_SEED_PASSWORD", ""))
    except SeedError as exc:
        raise click.ClickException(str(exc)) from None
    for email, plan, state, admin, verified in rows:
        flags = []
        if admin:
            flags.append("админ")
        if not verified:
            flags.append("без проверки личности")
        suffix = f" ({', '.join(flags)})" if flags else ""
        click.echo(f"{state:>8}  {email:<28} тариф: {plan}{suffix}")
    click.echo("Пароль — TEST_SEED_PASSWORD из .env.")


@click.command("close-auctions")
def close_auctions_command():
    """Закрывает лоты, у которых вышло время. Запускается планировщиком."""
    from .extensions import db
    from .services.auction_engine import close_due_lots

    from .services.auction_notify import notify_closed

    closed = close_due_lots()
    db.session.commit()
    for lot in closed:  # письма с итогами после commit: сбой почты не откатывает закрытие
        notify_closed(lot)
    click.echo(f"Закрыто лотов: {len(closed)}")


@click.command("send-onboarding-nudges")
def send_onboarding_nudges_command():
    """Одно письмо участникам, которые не заполнили профиль. Запускается планировщиком раз в час."""
    from .services import onboarding

    with current_app.test_request_context():
        click.echo(f"Напоминаний отправлено: {onboarding.run()}")


@click.command("reembed")
def reembed_command():
    """Пересчитывает эмбеддинги всех профилей, у которых изменился текст или провайдер."""
    from .extensions import db
    from .models import Profile
    from .services.matching import refresh_embeddings

    changed = 0
    for profile in Profile.query.all():
        if refresh_embeddings(profile):
            changed += 1
    db.session.commit()
    click.echo(f"Пересчитано профилей: {changed}")


@click.command("ai-check")
def ai_check_command():
    """Показывает, какие модели настроены, и проверяет ответ и эмбеддинги. Ключи не выводятся."""
    from .services.ai import AIUnavailable, ask, get_embedder, status

    for key, value in status().items():
        click.echo(f"{key:<20} {value}")
    try:
        answer = ask("Reply with one word.", "Say ok.", max_tokens=16, feature="check")
        click.echo(f"LLM-Antwort: {answer.strip()[:40] or '(leer)'}")
    except AIUnavailable as exc:
        click.echo(f"LLM: nicht verfügbar ({exc})")
    try:
        vectors = get_embedder().embed(["test"])
        click.echo(f"Embeddings: ok, Dimension {len(vectors[0])}")
    except AIUnavailable as exc:
        click.echo(f"Embeddings: nicht verfügbar ({exc})")


@click.command("seed-demo-profiles")
@click.option("--production", is_flag=True, help="Разрешить запуск в production (демо-участники помечаются «(Demo)»).")
def seed_demo_profiles_command(production):
    """Создаёт вымышленных демо-участников с заполненными анкетами."""
    from .services.seed import SeedError, seed_demo_profiles

    try:
        created = seed_demo_profiles(allow_production=production)
    except SeedError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"Демо-участников создано: {created}")


@click.command("seed-services")
def seed_services_command():
    """Добавляет стандартные услуги партнёра (повторный запуск безопасен)."""
    from .services.catalog_defaults import seed_default_services

    click.echo(f"Услуг добавлено: {seed_default_services()}")


@click.command("laws-import")
@click.option("--laws-dir", required=True, type=click.Path(exists=True, file_okay=False), help="Папка laws_data старого сервиса.")
@click.option("--qdrant", required=True, help="Адрес Qdrant старого сервиса, например http://qdrant:6333.")
@click.option("--no-index", is_flag=True, help="Не строить HNSW-индекс после загрузки.")
def laws_import_command(laws_dir, qdrant, no_index):
    """Переносит корпус законов (тексты и готовые векторы) из старого сервиса. Повторный запуск безопасен."""
    from pathlib import Path

    from .services.laws.importer import build_vector_index, import_laws, import_vectors

    laws = import_laws(Path(laws_dir), progress=click.echo)
    click.echo(f"Законов загружено: {laws}")
    chunks = import_vectors(qdrant, progress=click.echo)
    click.echo(f"Кусков с векторами загружено: {chunks}")
    if not no_index:
        build_vector_index(progress=click.echo)


@click.command("laws-index")
def laws_index_command():
    """Строит HNSW-индекс по векторам корпуса законов (если ещё нет)."""
    from .services.laws.importer import build_vector_index

    build_vector_index(progress=click.echo)


@click.command("laws-sync")
@click.option("--only", multiple=True, help="Только эти slug (можно несколько раз).")
@click.option("--limit", type=int, default=None, help="Первые N законов оглавления (для проверки).")
@click.option("--no-embed", is_flag=True, help="Не считать векторы (поиск будет полнотекстовым).")
def laws_sync_command(only, limit, no_embed):
    """Синхронизация корпуса с gesetze-im-internet.de: скачивает, разбирает и векторизует только изменившееся."""
    from .services.laws.sync import sync

    summary = sync(only=list(only) or None, limit=limit, embed_new=not no_embed, progress=click.echo)
    click.echo(f"Новых: {summary['new']}, обновлено: {summary['updated']}, без изменений: {summary['unchanged']}, "
               f"ошибок: {len(summary['errors'])}, секунд: {summary['seconds']}")
    for error in summary["errors"]:
        click.echo(f"  ! {error}")


@click.command("laws-sync-if-due")
def laws_sync_if_due_command():
    """Для планировщика: запускает laws-sync по понедельникам ночью, если корпус уже загружен и сегодня ещё не обновляли."""
    from .services.laws.sync import sync, sync_due

    if sync_due():
        summary = sync()
        click.echo(f"laws-sync: новых {summary['new']}, обновлено {summary['updated']}, ошибок {len(summary['errors'])}")


@click.command("laws-status")
def laws_status_command():
    """Состояние корпуса законов."""
    from .extensions import db
    from .models import LawSyncState
    from .services.laws.search import corpus_stats

    stats = corpus_stats()
    state = db.session.get(LawSyncState, 1)
    click.echo(f"Законов: {stats['laws']}, кусков: {stats['chunks']}, с векторами: {stats['vectors']}")
    click.echo(f"Последняя синхронизация: {state.last_run_at if state else 'ещё не было'}")


@click.command("stripe-setup")
@click.option("--apply", "apply_", is_flag=True, help="Создать недостающее. Без флага только показывает, что будет сделано.")
def stripe_setup_command(apply_):
    """Идемпотентно создаёт продукты, цены, вебхук и настройки портала в текущем аккаунте Stripe (тестовом или боевом)."""
    import stripe

    from config.plans import INTERVALS, PAID_PLANS, PLANS, price_cents

    from .services.billing import PRODUCT_APP, lookup_key

    key = current_app.config["STRIPE_SECRET_KEY"]
    if not key:
        raise click.ClickException("STRIPE_SECRET_KEY не задан в .env")
    live = key.startswith(("sk_live", "rk_live"))
    base = current_app.config["BASE_URL"].rstrip("/")
    click.echo(f"Режим Stripe: {'БОЕВОЙ' if live else 'тестовый'}; адрес клуба: {base}; "
               f"{'создаём' if apply_ else 'проверка без изменений (добавьте --apply)'}")

    products = {}
    existing = stripe.Product.list(api_key=key, active=True, limit=100)
    for product in existing.auto_paging_iter():
        meta = product.get("metadata") or {}
        if meta.get("app") == PRODUCT_APP and meta.get("plan") in PAID_PLANS:
            products[meta["plan"]] = product
    price_ids = {}
    for plan in PAID_PLANS:
        name = str(PLANS[plan]["name"])
        product = products.get(plan)
        if product is None:
            click.echo(f"+ продукт Club {name}")
            if not apply_:
                continue
            product = stripe.Product.create(api_key=key, name=f"Club {name}", metadata={"app": PRODUCT_APP, "plan": plan})
        for interval in INTERVALS:
            lookup = lookup_key(plan, interval)
            amount = price_cents(plan, interval)
            found = stripe.Price.list(api_key=key, lookup_keys=[lookup], active=True, limit=1)["data"]
            if found and found[0]["unit_amount"] == amount and found[0]["product"] == product["id"]:
                price_ids[(plan, interval)] = found[0]["id"]
                continue
            click.echo(f"+ цена {lookup}: {amount / 100:.2f} EUR" + (" (заменяет старую)" if found else ""))
            if not apply_:
                continue
            created = stripe.Price.create(
                api_key=key, product=product["id"], currency="eur", unit_amount=amount, tax_behavior="inclusive",
                recurring={"interval": interval}, lookup_key=lookup, transfer_lookup_key=True,
                metadata={"app": PRODUCT_APP, "plan": plan},
            )
            price_ids[(plan, interval)] = created["id"]
            if found:
                stripe.Price.modify(found[0]["id"], api_key=key, active=False)

    wanted_events = ["identity.verification_session.verified", "identity.verification_session.requires_input",
                     "identity.verification_session.processing", "identity.verification_session.canceled",
                     "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"]
    url = f"{base}/webhooks/stripe"
    endpoint = next((e for e in stripe.WebhookEndpoint.list(api_key=key, limit=100).auto_paging_iter() if e["url"] == url), None)
    if endpoint is None:
        click.echo(f"+ вебхук {url}")
        if apply_:
            created = stripe.WebhookEndpoint.create(api_key=key, url=url, enabled_events=wanted_events,
                                                    description="Club")
            click.echo(f"STRIPE_WEBHOOK_SECRET={created['secret']}  <- запишите в .env (показывается один раз)")
    elif set(wanted_events) - set(endpoint["enabled_events"]) and "*" not in endpoint["enabled_events"]:
        click.echo("+ события вебхука")
        if apply_:
            merged = sorted(set(endpoint["enabled_events"]) | set(wanted_events))
            stripe.WebhookEndpoint.modify(endpoint["id"], api_key=key, enabled_events=merged)

    if apply_ and len(price_ids) == len(PAID_PLANS) * len(INTERVALS):
        portal = dict(
            business_profile={"headline": "Club", "privacy_policy_url": f"{base}/datenschutz",
                              "terms_of_service_url": f"{base}/nutzungsbedingungen"},
            features={
                "customer_update": {"enabled": True, "allowed_updates": ["name", "address", "tax_id"]},
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
                "subscription_update": {
                    "enabled": True, "default_allowed_updates": ["price"], "proration_behavior": "create_prorations",
                    "products": [{"product": products[plan]["id"] if plan in products else None,
                                  "prices": [price_ids[(plan, i)] for i in INTERVALS]} for plan in PAID_PLANS],
                },
            },
            metadata={"app": PRODUCT_APP},
        )
        for item in portal["features"]["subscription_update"]["products"]:
            if item["product"] is None:  # продукт создан только что: берём из цены
                item["product"] = stripe.Price.retrieve(item["prices"][0], api_key=key)["product"]
        current = next((c for c in stripe.billing_portal.Configuration.list(api_key=key, is_active=True, limit=100)
                        .auto_paging_iter() if (c.get("metadata") or {}).get("app") == PRODUCT_APP), None)
        if current is None:
            current = stripe.billing_portal.Configuration.create(api_key=key, **portal)
            click.echo("+ настройки портала")
        else:
            stripe.billing_portal.Configuration.modify(current["id"], api_key=key, **portal)
        click.echo(f"STRIPE_PORTAL_CONFIG={current['id']}  <- запишите в .env")
    click.echo("Готово." if apply_ else "Проверка завершена, ничего не изменено.")


def register(app):
    app.cli.add_command(laws_import_command)
    app.cli.add_command(laws_index_command)
    app.cli.add_command(laws_sync_command)
    app.cli.add_command(laws_sync_if_due_command)
    app.cli.add_command(laws_status_command)
    app.cli.add_command(seed_services_command)
    app.cli.add_command(ai_check_command)
    app.cli.add_command(seed_demo_profiles_command)
    app.cli.add_command(reembed_command)
    app.cli.add_command(i18n_cli)
    app.cli.add_command(create_admin)
    app.cli.add_command(seed_test_users_command)
    app.cli.add_command(close_auctions_command)
    app.cli.add_command(stripe_setup_command)
    app.cli.add_command(send_onboarding_nudges_command)
