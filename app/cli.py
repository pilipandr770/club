"""Команды Flask: переводы, администраторы, тестовые участники, закрытие аукционов."""

import os

import click
from babel.messages.frontend import CommandLineInterface
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

    closed = close_due_lots()
    db.session.commit()
    click.echo(f"Закрыто лотов: {closed}")


def register(app):
    app.cli.add_command(i18n_cli)
    app.cli.add_command(create_admin)
    app.cli.add_command(seed_test_users_command)
    app.cli.add_command(close_auctions_command)
