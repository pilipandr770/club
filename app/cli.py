"""Команды Flask: flask i18n extract | update | compile (обёртка над pybabel)."""

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


def register(app):
    app.cli.add_command(i18n_cli)
