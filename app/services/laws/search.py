"""Поиск по корпусу законов внутри клуба: прямая ссылка («§ 433 BGB»), векторный поиск, запасной полнотекстовый.

Результат — те же словари, что раньше отдавал внешний сервис laws-api: полный текст параграфа берётся из law_sections,
найденный кусок остаётся в поле matched_part.
"""

import re

from sqlalchemy import func, text

from ...extensions import db
from ...models import Law, LawChunk, LawSection
from . import embed

MAX_HITS = 8
MAX_SECTION_CHARS = 6000
_REF = re.compile(r"§+\s*(\d+\s?[a-z]?)\b(?:\s*(?:abs\.?|absatz)\s*\d+)?(?:\s*(?:s\.?|satz)\s*\d+)?\s*([A-Za-zÄÖÜäöüß][\wÄÖÜäöüß\-]{1,24})?",
                  re.IGNORECASE)


def corpus_ready() -> bool:
    return db.session.query(LawChunk.id).limit(1).first() is not None


def corpus_stats() -> dict:
    return {"laws": db.session.query(func.count(Law.slug)).scalar() or 0,
            "chunks": db.session.query(func.count(LawChunk.id)).scalar() or 0,
            "vectors": db.session.query(func.count(LawChunk.id)).filter(LawChunk.embedding.isnot(None)).scalar() or 0}


def _section(slug: str, enbez: str, probe: str = "") -> LawSection | None:
    candidates = LawSection.query.filter_by(slug=slug, enbez=enbez).filter(LawSection.text != "").all()
    if len(candidates) > 1 and probe:
        wanted = probe.strip()[:200]
        candidates = [c for c in candidates if wanted in c.text] or candidates
    return candidates[0] if candidates else None


def _direct_hits(query: str, category: str) -> list[dict]:
    match = _REF.search(query)
    if not match or not match.group(2):
        return []
    number = re.sub(r"\s+", "", match.group(1)).lower()
    abbreviation = match.group(2).lower()
    laws = (Law.query.filter(func.lower(Law.abbreviation) == abbreviation).all()
            or Law.query.filter(Law.slug.like(f"{abbreviation}%")).limit(3).all())
    hits = []
    for law in laws:
        if category and law.category != category:
            continue
        sections = (LawSection.query.filter(LawSection.slug == law.slug,
                                            func.replace(func.lower(LawSection.enbez), " ", "") == f"§{number}")
                    .filter(LawSection.text != "").limit(1).all())
        for section in sections:
            hits.append({"slug": law.slug, "enbez": section.enbez, "titel": section.titel, "text": section.text,
                         "score": 1.0})
    return hits


def _vector_rows(vector: list[float], category: str, limit: int) -> list[dict]:
    literal = "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
    db.session.execute(text("SELECT set_config('hnsw.ef_search', '100', true)"))
    rows = db.session.execute(text("""
        SELECT c.slug, c.enbez, c.titel, c.text, 1 - (c.embedding <=> CAST(:q AS vector)) AS score
        FROM law_chunks c JOIN law_docs d ON d.slug = c.slug
        WHERE c.embedding IS NOT NULL AND (:cat = '' OR d.category = :cat)
        ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :n"""), {"q": literal, "cat": category, "n": limit}).mappings().all()
    return [dict(row) for row in rows]


def _text_rows(query: str, category: str, limit: int) -> list[dict]:
    rows = db.session.execute(text("""
        SELECT c.slug, c.enbez, c.titel, c.text, ts_rank_cd(c.search_vector, q.query) AS score
        FROM law_chunks c JOIN law_docs d ON d.slug = c.slug,
             websearch_to_tsquery('german', :query) AS q(query)
        WHERE c.search_vector @@ q.query AND (:cat = '' OR d.category = :cat)
        ORDER BY score DESC LIMIT :n"""), {"query": query, "cat": category, "n": limit}).mappings().all()
    return [dict(row) for row in rows]


def search(query: str, category: str = "", k: int = 6) -> list[dict]:
    """Treffer mit vollem Paragraphentext. Reihenfolge: direkte Fundstelle, dann Vektorsuche (sonst Volltext)."""
    query = query.strip()[:800]
    k = max(1, min(MAX_HITS, k))
    raw = _direct_hits(query, category)
    vector = embed.embed_query(query) if embed.available() else None
    found = _vector_rows(vector, category, k * 5) if vector else []
    if not found:
        found = _text_rows(query, category, k * 5)
    laws = {law.slug: law for law in Law.query.filter(Law.slug.in_({r["slug"] for r in raw + found})).all()} if raw or found else {}
    out, seen = [], set()
    for row in raw + found:
        key = (row["slug"], row["enbez"] or row["titel"] or row["text"][:60])
        if key in seen or row["slug"] not in laws:
            continue
        seen.add(key)
        law = laws[row["slug"]]
        section = _section(row["slug"], row["enbez"], row["text"])
        full = (section.text if section else row["text"])[:MAX_SECTION_CHARS]
        out.append({"id": len(out) + 1, "slug": law.slug, "law_abbreviation": law.abbreviation, "law_title": law.title,
                    "category": law.category, "enbez": row["enbez"], "titel": row["titel"], "text": full,
                    "matched_part": row["text"], "stand": (section.stand if section and section.stand else law.stand) or [],
                    "source_url": law.source_url, "score": round(float(row["score"]), 3)})
        if len(out) >= k:
            break
    return out
