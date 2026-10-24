"""Ссылки участника на соцсети. Мы их только сохраняем и показываем: ничего не загружаем и не читаем по ним.

Ссылка принимается, если адрес относится к домену выбранной сети: так «Instagram» не ведёт на чужой сайт.
Для удобства принимается и короткое имя (@name), оно превращается в адрес сети.
Показ: принятым контактам всегда, остальным участникам только при согласии `social_links` (страница «Публикация»).
"""

import re
from urllib.parse import urlsplit

from flask_babel import lazy_gettext as _l

from .utils import clean_url

# ключ, название, домены, база для короткого имени (None: только полный адрес)
NETWORKS = (
    ("instagram", _l("Instagram"), ("instagram.com",), "https://www.instagram.com/"),
    ("facebook", _l("Facebook"), ("facebook.com", "fb.com"), "https://www.facebook.com/"),
    ("x", _l("X"), ("x.com", "twitter.com"), "https://x.com/"),
    ("telegram", _l("Telegram"), ("t.me", "telegram.me"), "https://t.me/"),
    ("youtube", _l("YouTube"), ("youtube.com", "youtu.be"), "https://www.youtube.com/@"),
    ("tiktok", _l("TikTok"), ("tiktok.com",), "https://www.tiktok.com/@"),
    ("github", _l("GitHub"), ("github.com",), "https://github.com/"),
    ("xing", _l("Xing"), ("xing.com",), None),
)
NETWORK_BY_KEY = {key: (label, domains, base) for key, label, domains, base in NETWORKS}
_HANDLE = re.compile(r"^@?([A-Za-z0-9_.\-]{1,60})$")


def normalize(network: str, raw: str):
    """Адрес для сети или None, если значение не подходит."""
    if network not in NETWORK_BY_KEY:
        return None
    _label, domains, base = NETWORK_BY_KEY[network]
    raw = (raw or "").strip()
    if not raw:
        return None
    handle = _HANDLE.match(raw)
    if handle and base:
        return (base + handle.group(1))[:300]
    url = clean_url(raw if "://" in raw else "https://" + raw)
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in domains):
        return None
    return url[:300]


def card_links(profile):
    """Все ссылки профиля в порядке показа: [(название, адрес)]."""
    links = []
    if profile.website_url:
        links.append((_l("Website"), profile.website_url))
    if profile.linkedin_url:
        links.append((_l("Professional profile"), profile.linkedin_url))
    saved = profile.social_links or {}
    for key, label, _domains, _base in NETWORKS:
        if saved.get(key):
            links.append((label, saved[key]))
    return links
