import os

from flask import Flask
from flask_babel import get_locale
from werkzeug.middleware.proxy_fix import ProxyFix

from . import cli
from .config import CONFIGS
from .extensions import babel, csrf, db, limiter, login_manager, migrate
from .i18n import select_locale, select_timezone
from .services.identity import StripeIdentityProvider

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
    "form-action 'self' https://checkout.stripe.com https://verify.stripe.com; "
    "frame-ancestors 'none'"
)


def create_app(env=None):
    env = env or os.environ.get("APP_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[env])
    if env == "production" and not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY не задан: в production без него нельзя запускаться")

    # Единственный вход — Caddy, он один стоит перед приложением
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    migrate.init_app(app, db)
    babel.init_app(
        app,
        locale_selector=select_locale,
        timezone_selector=select_timezone,
    )
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User

        return db.session.get(User, int(user_id))

    app.extensions["identity_provider"] = StripeIdentityProvider(
        app.config["STRIPE_SECRET_KEY"], app.config["STRIPE_WEBHOOK_SECRET"]
    )

    from .blueprints.auth import bp as auth_bp
    from .blueprints.member import bp as member_bp
    from .blueprints.public import bp as public_bp
    from .blueprints.webhooks import bp as webhooks_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(member_bp)
    app.register_blueprint(webhooks_bp)

    from . import models  # noqa: F401  (регистрирует модели для миграций)

    cli.register(app)

    # Текущий язык для <html lang="..."> и current_user в шаблонах
    app.jinja_env.globals["get_locale"] = get_locale

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app
