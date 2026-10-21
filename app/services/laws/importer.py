"""Одноразовый перенос готового корпуса из старого сервиса (тексты законов + векторы из Qdrant): без новой оплаты эмбеддингов.

Запуск (на сервере): docker compose run --rm -v laws_pipeline_laws_data:/laws:ro app flask laws-import --laws-dir /laws --qdrant http://qdrant:6333
Команда безопасна к повторному запуску: готовые законы и куски пропускаются.
"""

import hashlib
import json
import urllib.request
from pathlib import Path

from sqlalchemy import text

from ...extensions import db
from ...models import Law, LawSection
from ...utils import utcnow
from .parse import is_gone

SCROLL_BATCH = 400


def import_laws(laws_dir: Path, progress=print) -> int:
    """Законы и параграфы из файлов <slug>.json старого корпуса."""
    manifest = {}
    manifest_path = laws_dir / "_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    have = {slug for (slug,) in db.session.query(Law.slug)}
    count = 0
    for path in sorted(laws_dir.glob("*.json")):
        if path.name.startswith("_") or path.stem in have:
            continue
        try:
            law = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        slug = law.get("slug") or path.stem
        info = manifest.get(slug, {})
        db.session.add(Law(slug=slug, abbreviation=(law.get("law_abbreviation") or slug)[:120],
                           title=(law.get("law_title") or "")[:400], category=law.get("category") or "Unclassified",
                           source_url=(law.get("source_url") or "")[:300], stand=law.get("stand") or [],
                           sha256=info.get("sha256", ""), fetched_at=utcnow()))
        db.session.flush()
        db.session.bulk_insert_mappings(LawSection, [
            {"slug": slug, "position": i, "enbez": (s.get("enbez") or "")[:80], "titel": (s.get("titel") or "")[:400],
             "text": s.get("text") or "", "stand": s.get("stand") or law.get("stand") or []}
            for i, s in enumerate(law.get("sections", []))])
        count += 1
        if count % 200 == 0:
            db.session.commit()
            progress(f"Gesetze: {count}")
    db.session.commit()
    return count


def _scroll(qdrant: str, collection: str, offset):
    body = {"limit": SCROLL_BATCH, "with_payload": True, "with_vector": True}
    if offset is not None:
        body["offset"] = offset
    request = urllib.request.Request(f"{qdrant.rstrip('/')}/collections/{collection}/points/scroll",
                                     data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())["result"]


def import_vectors(qdrant: str, collection: str = "german_laws", progress=print) -> int:
    """Куски с готовыми векторами из Qdrant. Возвращает число добавленных."""
    known_laws = {slug for (slug,) in db.session.query(Law.slug)}
    insert = text("""
        INSERT INTO law_chunks (id, slug, enbez, titel, part, text, text_hash, embedding)
        VALUES (:id, :slug, :enbez, :titel, :part, :text, :text_hash, CAST(:embedding AS vector))
        ON CONFLICT (id) DO NOTHING""")
    added, offset = 0, None
    while True:
        page = _scroll(qdrant, collection, offset)
        rows = []
        for point in page["points"]:
            payload = point.get("payload") or {}
            body = (payload.get("text") or "").strip()
            if payload.get("slug") not in known_laws or is_gone(body) or not point.get("vector"):
                continue
            rows.append({"id": str(point["id"]), "slug": payload["slug"], "enbez": (payload.get("enbez") or "")[:80],
                         "titel": (payload.get("titel") or "")[:400], "part": payload.get("part") or "",
                         "text": body, "text_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                         "embedding": "[" + ",".join(f"{x:.7g}" for x in point["vector"]) + "]"})
        if rows:
            db.session.execute(insert, rows)
            db.session.commit()
            added += len(rows)
        offset = page.get("next_page_offset")
        progress(f"Vektoren: {added}")
        if offset is None:
            return added


def build_vector_index(progress=print) -> None:
    """HNSW-индекс по косинусу. Строится после загрузки: так на порядок быстрее, чем при вставке."""
    progress("HNSW-Index wird gebaut (einige Minuten) ...")
    db.session.execute(text("SET maintenance_work_mem = '1GB'"))
    db.session.execute(text("SET max_parallel_maintenance_workers = 2"))
    db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_law_chunks_embedding ON law_chunks "
                            "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"))
    db.session.commit()
    db.session.execute(text("ANALYZE law_chunks"))
    db.session.execute(text("ANALYZE law_sections"))
    db.session.commit()
