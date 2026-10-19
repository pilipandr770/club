import os

from flask import Flask
from flask_babel import get_locale

from . import cli
from .config import CONFIGS
from .extensions import babel, csrf, db, migrate
from .i18n import select_locale, select_timezone

# CSP: без inline-скриптов и стилей, без внешних CDN (CLAUDE.md, раздел «Интерфейс и безопасность»)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def create_app(env=None):
    env = env or os.environ.get("APP_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[env])
    if env == "production" and not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY не задан: в production без него нельзя запускаться")

    db.init_app(app)
    migrate.init_app(app, db)
    babel.init_app(
        app,
        locale_selector=select_locale,
        timezone_selector=select_timezone,
    )
    csrf.init_app(app)

    from .blueprints.public import bp as public_bp

    app.register_blueprint(public_bp)

    from . import models  # noqa: F401  (регистрирует модели для миграций)

    cli.register(app)

    # Текущий язык для <html lang="..."> в шаблонах
    app.jinja_env.globals["get_locale"] = get_locale

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app
