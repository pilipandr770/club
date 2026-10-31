"""Синхронизация корпуса с gesetze-im-internet.de: оглавление, скачивание изменившихся законов, куски, векторы.

Скачиваются все zip (хеш сравнивается с сохранённым), но разбирается и векторизуется только изменившееся.
К сайту вежливы: несколько потоков с паузой, понятный User-Agent. Запись в БД идёт только из основного потока.
"""

import hashlib
import io
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from ...extensions import db
from ...models import Law, LawChunk, LawSection, LawSyncState
from ...utils import utcnow
from . import embed
from .parse import chunks_of, merge_parsed, parse_law_xml, slugify

log = logging.getLogger(__name__)

TOC_URL = "https://www.gesetze-im-internet.de/gii-toc.xml"
USER_AGENT = "Mozilla/5.0 (compatible; ClubLawsSync/1.0; contact info@andrii-it.de)"
MAX_ZIP_BYTES = 60 * 1024 * 1024


def _download(url: str, timeout: int = 40) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_ZIP_BYTES + 1)[:MAX_ZIP_BYTES]


def fetch_toc() -> list[tuple[str, str]]:
    """Оглавление: [(название, адрес zip)]."""
    root = ET.fromstring(_download(TOC_URL))
    items = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if link:
            items.append(((item.findtext("title") or "").strip(), link))
    return items


def law_from_zip(slug: str, zip_url: str, content: bytes) -> dict | None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        parsed = []
        for name in archive.namelist():
            if name.lower().endswith(".xml"):
                try:
                    parsed.append(parse_law_xml(archive.read(name), slug, zip_url))
                except ET.ParseError:
                    continue
    return merge_parsed(slug, zip_url, parsed) if parsed else None


def store_law(law: dict, sha256: str, embed_new: bool = True) -> str:
    """Записывает закон и пересобирает его куски. Векторы считаются только для новых и изменившихся кусков."""
    slug = law["slug"]
    existing = db.session.get(Law, slug)
    status = "updated" if existing else "new"
    row = existing or Law(slug=slug)
    jurisdiction = law.get("jurisdiction", "DE")
    row.jurisdiction, row.revision = jurisdiction, str(law.get("revision", ""))[:40]
    row.abbreviation, row.title = law["law_abbreviation"][:120], law["law_title"][:400]
    row.category, row.source_url = law["category"], law["source_url"][:300]
    row.stand, row.sha256, row.fetched_at = law["stand"], sha256, utcnow()
    db.session.add(row)
    db.session.flush()

    LawSection.query.filter_by(slug=slug).delete()
    db.session.bulk_insert_mappings(LawSection, [
        {"slug": slug, "position": i, "enbez": s["enbez"][:80], "titel": s["titel"][:400], "text": s["text"],
         "stand": s["stand"] or law["stand"]} for i, s in enumerate(law["sections"])])

    wanted = {c[0]: c for c in chunks_of(law)}
    old = {cid: digest for cid, digest in db.session.query(LawChunk.id, LawChunk.text_hash).filter_by(slug=slug)}
    stale = [cid for cid in old if cid not in wanted]
    if stale:
        LawChunk.query.filter(LawChunk.id.in_(stale)).delete(synchronize_session=False)
    changed = [c for cid, c in wanted.items() if old.get(cid) != c[5]]
    if changed:
        LawChunk.query.filter(LawChunk.id.in_([c[0] for c in changed])).delete(synchronize_session=False)
        vectors = embed.embed_texts([c[4] for c in changed]) if embed_new else [None] * len(changed)
        db.session.bulk_insert_mappings(LawChunk, [
            {"id": cid, "slug": slug, "jurisdiction": jurisdiction, "enbez": enbez[:80], "titel": titel[:400],
             "part": part, "text": text, "text_hash": digest, "embedding": vector}
            for (cid, enbez, titel, part, text, digest), vector in zip(changed, vectors)])
    db.session.commit()
    return status


def sync(only: list[str] | None = None, limit: int | None = None, workers: int = 4, delay: float = 0.2,
         embed_new: bool = True, progress=None) -> dict:
    """Полная синхронизация. Возвращает сводку: new/updated/unchanged/errors."""
    started = time.monotonic()
    items = fetch_toc()
    if only:
        items = [i for i in items if slugify(i[1]) in set(only)]
    if limit:
        items = items[:limit]
    known = {slug: sha for slug, sha in db.session.query(Law.slug, Law.sha256).filter(Law.jurisdiction == "DE")}
    summary = {"new": 0, "updated": 0, "unchanged": 0, "errors": [], "total": len(items)}

    def work(item):
        _title, url = item
        time.sleep(delay)
        content = _download(url)
        return slugify(url), url, content

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, item): item for item in items}
        for number, future in enumerate(as_completed(futures), 1):
            slug = slugify(futures[future][1])
            try:
                slug, url, content = future.result()
                digest = hashlib.sha256(content).hexdigest()
                if known.get(slug) == digest:
                    summary["unchanged"] += 1
                    continue
                law = law_from_zip(slug, url, content)
                if law is None:
                    summary["errors"].append(f"{slug}: kein XML")
                    continue
                summary[store_law(law, digest, embed_new)] += 1
            except Exception as exc:  # noqa: BLE001 - ein kaputtes Gesetz bricht die Synchronisation nicht ab
                db.session.rollback()
                summary["errors"].append(f"{slug}: {str(exc)[:120]}")
                log.warning("Gesetz %s fehlgeschlagen: %s", slug, exc)
            if progress and number % 200 == 0:
                progress(f"{number}/{len(items)}")
    if not only and not limit and not summary["errors"]:  # ушедшие из оглавления законы убираем
        current = {slugify(url) for _t, url in items}
        for slug in [s for s in known if s not in current]:
            Law.query.filter_by(slug=slug).delete()
        db.session.commit()
    summary["errors"] = summary["errors"][:10]
    summary["seconds"] = round(time.monotonic() - started)
    state = db.session.get(LawSyncState, 1) or LawSyncState(id=1)
    state.last_run_at, state.summary = utcnow(), summary
    db.session.add(state)
    db.session.commit()
    return summary


def sync_due(now=None) -> bool:
    """Нужна ли еженедельная синхронизация: понедельник ночью (UTC), сегодня ещё не запускали, корпус уже загружен."""
    now = now or utcnow()
    if now.weekday() != 0 or not 1 <= now.hour <= 5:
        return False
    state = db.session.get(LawSyncState, 1)
    if state and state.last_run_at and now - state.last_run_at < timedelta(hours=20):
        return False
    return db.session.query(Law.slug).filter(Law.jurisdiction == "DE").limit(1).first() is not None
