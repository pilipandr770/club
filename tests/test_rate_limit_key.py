"""Лимиты запросов считаются по адресу посетителя из Cloudflare, а не по адресу прокси."""

from app.extensions import client_key


def test_key_is_the_cloudflare_visitor_address(app):
    with app.test_request_context("/", headers={"CF-Connecting-IP": "203.0.113.1", "X-Forwarded-For": "10.0.0.2"}):
        assert client_key() == "203.0.113.1"
    with app.test_request_context("/", headers={"CF-Connecting-IP": "198.51.100.2", "X-Forwarded-For": "10.0.0.2"}):
        assert client_key() == "198.51.100.2"  # разные посетители за одним прокси не делят лимит


def test_key_falls_back_to_connection_address_without_cloudflare(app):
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "192.0.2.9"}):
        assert client_key() == "192.0.2.9"


def test_key_is_bounded(app):
    with app.test_request_context("/", headers={"CF-Connecting-IP": "9" * 500}):
        assert len(client_key()) <= 64
