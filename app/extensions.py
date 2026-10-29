from flask_babel import Babel
from sqlalchemy import MetaData
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

# Имена ограничений задаём сами: без них миграции не смогут их удалить (Alembic предупреждает)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
migrate = Migrate()
babel = Babel()
csrf = CSRFProtect()
login_manager = LoginManager()
def client_key():
    """Ключ лимитов: адрес посетителя. Перед приложением стоят Cloudflare, Traefik и Caddy, поэтому remote_addr
    и X-Forwarded-For показывают адрес прокси (у всех посетителей один и тот же). Cloudflare передаёт настоящий
    адрес в CF-Connecting-IP; без него (локальная разработка, тесты) берём обычный адрес соединения."""
    from flask import request

    forwarded = request.headers.get("CF-Connecting-IP", "").strip()
    return forwarded[:64] if forwarded else get_remote_address()


limiter = Limiter(key_func=client_key)
