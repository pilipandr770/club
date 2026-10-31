"""Поиск по корпусу законов внутри клуба: прямая ссылка («§ 433 BGB»), векторный поиск, запасной полнотекстовый.

Результат — те же словари, что раньше отдавал внешний сервис laws-api: полный текст параграфа берётся из law_sections,
найденный кусок остаётся в поле matched_part.
"""

import re

from sqlalchemy import func, text

from ...extensions import db
from ...jurisdictions import get_pack
from ...jurisdictions.il.catalog import BY_SLUG as IL_LAWS
from ...models import Law, LawChunk, LawSection
from ..ai import llm
from ..ai.http import AIUnavailable
from . import embed

MAX_HITS = 8
MAX_SECTION_CHARS = 6000
_REF = re.compile(r"§+\s*(\d+\s?[a-z]?)\b(?:\s*(?:abs\.?|absatz)\s*\d+)?(?:\s*(?:s\.?|satz)\s*\d+)?\s*([A-Za-zÄÖÜäöüß][\wÄÖÜäöüß\-]{1,24})?",
                  re.IGNORECASE)
_IL_REF = re.compile(r"(?:סעיף|סעיפים|section|sec\.?|article|art\.?|статья|ст\.|§)\s*(\d+[א-ת]?\d*)", re.IGNORECASE)
_HE_WORD = re.compile(r"[א-ת]{3,}")
_HE_PREFIXES = "ובכלמשה"
_HE_STOP = {"מהו", "מהי", "האם", "אילו", "איזה", "איזו", "לפי", "על", "או", "את", "זה", "זו", "הם", "הן", "כל", "מה",
            "אשר", "שלי", "שלו", "עם", "אין", "יש", "גם", "כי", "לא"}


def corpus_ready(jurisdiction: str = "DE") -> bool:
    return db.session.query(LawChunk.id).filter(LawChunk.jurisdiction == jurisdiction).limit(1).first() is not None


def ready_jurisdictions() -> list[str]:
    """Коды стран, для которых корпус загружен и пакет включён."""
    loaded = {code for (code,) in db.session.query(LawChunk.jurisdiction).distinct()}
    return [code for code in ("DE", "IL") if code in loaded and get_pack(code).enabled]


def corpus_stats(jurisdiction: str | None = None) -> dict:
    laws, chunks = db.session.query(func.count(Law.slug)), db.session.query(func.count(LawChunk.id))
    if jurisdiction:
        laws, chunks = laws.filter(Law.jurisdiction == jurisdiction), chunks.filter(LawChunk.jurisdiction == jurisdiction)
    return {"laws": laws.scalar() or 0, "chunks": chunks.scalar() or 0,
            "vectors": chunks.filter(LawChunk.embedding.isnot(None)).scalar() or 0}


def corpus_laws(jurisdiction: str) -> list[Law]:
    """Законы корпуса страны: обзор «что именно ищет инструмент» (юрист видит состав и ревизию источника)."""
    return Law.query.filter_by(jurisdiction=jurisdiction).order_by(Law.category, Law.abbreviation).all()


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
    german = Law.query.filter(Law.jurisdiction == "DE")
    laws = (german.filter(func.lower(Law.abbreviation) == abbreviation).all()
            or german.filter(Law.slug.like(f"{abbreviation}%")).limit(3).all())
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


def _direct_hits_il(query: str, category: str) -> list[dict]:
    """«סעיף 2 לחוק הגנת הפרטיות» или «section 2 privacy protection law»: статья названного закона."""
    match = _IL_REF.search(query)
    if not match:
        return []
    number = re.sub(r"\s+", "", match.group(1))
    lowered = query.lower()
    scored = []
    for law in Law.query.filter_by(jurisdiction="IL").all():
        names = [law.abbreviation, *(IL_LAWS[law.slug].aliases if law.slug in IL_LAWS else ())]
        length = max((len(n) for n in names if n and n.lower() in lowered), default=0)
        if length and (not category or law.category == category):
            scored.append((length, law))
    if not scored:
        return []
    best = max(length for length, _law in scored)
    hits = []
    for _length, law in (item for item in scored if item[0] == best):
        for section in (LawSection.query.filter_by(slug=law.slug, enbez=f"סעיף {number}")
                        .filter(LawSection.text != "").limit(1).all()):
            hits.append({"slug": law.slug, "enbez": section.enbez, "titel": section.titel, "text": section.text,
                         "score": 1.0})
    return hits


def _vector_rows(vector: list[float], category: str, limit: int, jurisdiction: str) -> list[dict]:
    literal = "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
    code = get_pack(jurisdiction).code  # проверенный код из реестра: подставляем литералом, чтобы сработал частичный индекс
    db.session.execute(text("SELECT set_config('hnsw.ef_search', '100', true)"))
    rows = db.session.execute(text(f"""
        SELECT c.slug, c.enbez, c.titel, c.text, 1 - (c.embedding <=> CAST(:q AS vector)) AS score
        FROM law_chunks c JOIN law_docs d ON d.slug = c.slug
        WHERE c.embedding IS NOT NULL AND c.jurisdiction = '{code}' AND (:cat = '' OR d.category = :cat)
        ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :n"""), {"q": literal, "cat": category, "n": limit}).mappings().all()
    return [dict(row) for row in rows]


def _text_rows(query: str, category: str, limit: int, jurisdiction: str) -> list[dict]:
    if jurisdiction == "IL":
        return _hebrew_rows(query, category, limit)
    rows = db.session.execute(text("""
        SELECT c.slug, c.enbez, c.titel, c.text, ts_rank_cd(c.search_vector, q.query) AS score
        FROM law_chunks c JOIN law_docs d ON d.slug = c.slug,
             websearch_to_tsquery('german', :query) AS q(query)
        WHERE c.search_vector @@ q.query AND c.jurisdiction = :jur AND (:cat = '' OR d.category = :cat)
        ORDER BY score DESC LIMIT :n"""), {"query": query, "cat": category, "n": limit, "jur": jurisdiction}).mappings().all()
    return [dict(row) for row in rows]


def hebrew_terms(query: str) -> list[str]:
    """Слова запроса без одной приставки (ב ה ו כ ל מ ש): подстрока находит и «הפרטיות», и «בפרטיות»."""
    terms = []
    for word in _HE_WORD.findall(query):
        if word in _HE_STOP:
            continue
        terms.append(word[1:] if len(word) >= 4 and word[0] in _HE_PREFIXES else word)
    return list(dict.fromkeys(terms))[:8]


def _hebrew_rows(query: str, category: str, limit: int) -> list[dict]:
    """Запасной поиск для иврита без векторов: в Postgres нет ивритской морфологии, а корпус небольшой (тысячи кусков)."""
    terms = hebrew_terms(query)
    if not terms:
        return []
    params = {f"t{i}": "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
              for i, term in enumerate(terms)}
    score = " + ".join(f"CASE WHEN c.text ILIKE :t{i} THEN 1 ELSE 0 END" for i in range(len(terms)))
    rows = db.session.execute(text(f"""
        SELECT c.slug, c.enbez, c.titel, c.text, ({score})::float / {len(terms)} AS score
        FROM law_chunks c JOIN law_docs d ON d.slug = c.slug
        WHERE c.jurisdiction = 'IL' AND (:cat = '' OR d.category = :cat) AND ({score}) > 0
        ORDER BY score DESC, c.slug, c.enbez LIMIT :n"""), {**params, "cat": category, "n": limit}).mappings().all()
    return [dict(row) for row in rows]


_TO_HEBREW = ("Translate the user's legal question or keywords into Hebrew, using the terms that appear in Israeli statutes. "
              "Output only the Hebrew text, at most 40 words, no explanation.")


def hebrew_query(query: str) -> str:
    """Вопрос на немецком/русском/английском → иврит для векторного поиска. Без ИИ или при сбое — исходный текст."""
    if _HE_WORD.search(query):
        return query
    try:
        translated = llm.complete(_TO_HEBREW, query, max_tokens=150, timeout=20, feature="law_translate").strip()
    except AIUnavailable:
        return query
    return translated[:400] if _HE_WORD.search(translated) else query


def search(query: str, category: str = "", k: int = 6, jurisdiction: str = "DE") -> list[dict]:
    """Treffer mit vollem Paragraphentext. Reihenfolge: direkte Fundstelle, dann Vektorsuche (sonst Volltext).

    Das Land wird ausdrücklich angegeben; ein unbekanntes Land ist ein Fehler (UnknownJurisdiction)."""
    get_pack(jurisdiction)
    query = query.strip()[:800]
    k = max(1, min(MAX_HITS, k))
    raw = _direct_hits_il(query, category) if jurisdiction == "IL" else _direct_hits(query, category)
    topic = hebrew_query(query) if jurisdiction == "IL" else query  # корпус на иврите: ищем по ивритской формулировке
    vector = embed.embed_query(topic) if embed.available() else None
    found = _vector_rows(vector, category, k * 5, jurisdiction) if vector else []
    if not found:
        found = _text_rows(topic, category, k * 5, jurisdiction)
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
        out.append({"id": len(out) + 1, "slug": law.slug, "jurisdiction": law.jurisdiction, "revision": law.revision,
                    "source_version": (law.stand or [""])[0] if law.jurisdiction != "DE" else "",
                    "law_abbreviation": law.abbreviation, "law_title": law.title, "category": law.category, "enbez": row["enbez"], "titel": row["titel"], "text": full,
                    "matched_part": row["text"], "stand": (section.stand if section and section.stand else law.stand if law.jurisdiction == "DE" else []) or [],
                    "source_url": law.source_url, "score": round(float(row["score"]), 3)})
        if len(out) >= k:
            break
    return out
