import pytest

from app import create_app
from app.extensions import db


@pytest.fixture(scope="session")
def app():
    application = create_app("test")
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_tables(app):
    """Каждый тест начинается с пустых таблиц."""
    yield
    with app.app_context():
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()
