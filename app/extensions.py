from flask_babel import Babel
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
babel = Babel()
csrf = CSRFProtect()
