"""Загрузка законов Израиля с Hebrew Wikisource (ספר החוקים הפתוח) через API MediaWiki.

Сначала одним запросом узнаём номера ревизий всех страниц каталога; страницы, у которых ревизия не изменилась, не качаем.
К сайту вежливы: понятный User-Agent, пауза между запросами. Текст законов — общественное достояние, разметка и сводка
Open Law Book — CC BY-SA 4.0 (указываем источник и ревизию у каждого закона).
"""

import json
import time
import urllib.parse
import urllib.request

from .catalog import CATALOG, IlLaw
from .wikitext import parse_law

API = "https://he.wikisource.org/w/api.php"
USER_AGENT = "ClubLawsSync/1.0 (https://club.andrii-it.de; info@andrii-it.de)"
BATCH = 40
MAX_BYTES = 8 * 1024 * 1024


def _api(**params) -> dict:
    params["format"] = "json"
    request = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read(MAX_BYTES).decode("utf-8"))


def fetch_revisions(laws: list[IlLaw] | None = None) -> dict[str, tuple[int, str]]:
    """{страница: (ревизия, время)} для страниц каталога. Отсутствующие страницы в ответ не попадают."""
    laws = laws or list(CATALOG)
    out = {}
    for start in range(0, len(laws), BATCH):
        data = _api(action="query", prop="revisions", rvprop="ids|timestamp", redirects=1,
                    titles="|".join(law.page for law in laws[start:start + BATCH]))
        redirects = {r["to"]: r["from"] for r in data["query"].get("redirects", [])}
        for page in data["query"]["pages"].values():
            if "revisions" in page:
                revision = page["revisions"][0]
                out[redirects.get(page["title"], page["title"])] = (revision["revid"], revision["timestamp"])
        time.sleep(0.2)
    return out


def fetch_law(law: IlLaw, delay: float = 0.3) -> dict:
    """Скачивает и разбирает один закон (формат `services/laws/sync.store_law`)."""
    time.sleep(delay)
    data = _api(action="query", prop="revisions", rvprop="content|ids|timestamp", rvslots="main", redirects=1,
                titles=law.page)
    for page in data["query"]["pages"].values():
        if "revisions" in page:
            revision = page["revisions"][0]
            return parse_law(revision["slots"]["main"]["*"], law.slug, law.page, revision["revid"],
                             revision["timestamp"], law.category)
    raise LookupError(f"Seite nicht gefunden: {law.page}")
