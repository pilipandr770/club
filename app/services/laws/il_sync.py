"""Синхронизация израильского корпуса с Hebrew Wikisource (ספר החוקים הפתוח).

Ревизии страниц проверяются одним запросом; скачиваются, разбираются и векторизуются только изменившиеся законы.
Состояние последнего запуска — строка 2 в `law_sync_state` (строка 1 — немецкий корпус).
"""

import hashlib
import json
import logging
import time
from datetime import timedelta

from sqlalchemy import text

from ...extensions import db
from ...jurisdictions.il import source
from ...jurisdictions.il.catalog import BY_SLUG, CATALOG
from ...models import Law, LawSyncState
from ...utils import utcnow
from .sync import store_law

log = logging.getLogger(__name__)

STATE_ID = 2


def sync(only: list[str] | None = None, embed_new: bool = True, delay: float = 0.3, progress=None,
         force: bool = False) -> dict:
    """Возвращает сводку: new/updated/unchanged/errors. force: перечитать и пересобрать, даже если ревизия та же (после правки разбора)."""
    started = time.monotonic()
    laws = [law for law in CATALOG if not only or law.slug in only]
    summary = {"new": 0, "updated": 0, "unchanged": 0, "errors": [], "total": len(laws)}
    revisions = source.fetch_revisions(laws)
    known = {slug: revision for slug, revision in db.session.query(Law.slug, Law.revision).filter(Law.jurisdiction == "IL")}
    for number, law in enumerate(laws, 1):
        try:
            if law.page not in revisions:
                summary["errors"].append(f"{law.slug}: Seite nicht gefunden")
                continue
            if not force and known.get(law.slug) == str(revisions[law.page][0]):
                summary["unchanged"] += 1
                continue
            parsed = source.fetch_law(law, delay)
            if not any(s["text"] for s in parsed["sections"]):
                summary["errors"].append(f"{law.slug}: kein Text erkannt")
                continue
            digest = hashlib.sha256(json.dumps(parsed["sections"], ensure_ascii=False).encode("utf-8")).hexdigest()
            summary[store_law(parsed, digest, embed_new)] += 1
        except Exception as exc:  # noqa: BLE001 - ein kaputtes Gesetz bricht die Synchronisation nicht ab
            db.session.rollback()
            summary["errors"].append(f"{law.slug}: {str(exc)[:120]}")
            log.warning("Gesetz %s fehlgeschlagen: %s", law.slug, exc)
        if progress:
            progress(f"{number}/{len(laws)} {law.slug}")
    if not only and not summary["errors"]:  # законы, ушедшие из каталога, убираем
        for slug in [s for s in known if s not in BY_SLUG]:
            Law.query.filter_by(slug=slug).delete()
        db.session.commit()
    summary["errors"] = summary["errors"][:10]
    summary["seconds"] = round(time.monotonic() - started)
    state = db.session.get(LawSyncState, STATE_ID) or LawSyncState(id=STATE_ID)
    state.last_run_at, state.summary = utcnow(), summary
    db.session.add(state)
    db.session.commit()
    return summary


def sync_due(now=None) -> bool:
    """Нужна ли еженедельная синхронизация: вторник ночью (UTC), сегодня ещё не запускали, корпус уже загружен."""
    now = now or utcnow()
    if now.weekday() != 1 or not 1 <= now.hour <= 5:
        return False
    state = db.session.get(LawSyncState, STATE_ID)
    if state and state.last_run_at and now - state.last_run_at < timedelta(hours=20):
        return False
    return db.session.query(Law.slug).filter(Law.jurisdiction == "IL").limit(1).first() is not None


def build_il_index() -> None:
    """Частичный HNSW-индекс по израильским векторам (идемпотентно; миграция создаёт его заранее)."""
    db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_law_chunks_embedding_il ON law_chunks "
                            "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64) "
                            "WHERE jurisdiction = 'IL'"))
    db.session.commit()
