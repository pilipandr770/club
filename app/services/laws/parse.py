"""Разбор XML законов с gesetze-im-internet.de и нарезка на куски для поиска.

Логика и идентификаторы кусков совпадают со старым сервисом (laws_pipeline), чтобы импортированные векторы
остались действительными: chunk_id зависит от slug, enbez, номера параграфа в законе и номера части.
"""

import hashlib
import re
import uuid
import xml.etree.ElementTree as ET

from .catalog import category_for

MAX_CHARS = 4000  # лимит модели эмбеддингов (таблицы токенизируются почти 1:1)
MAX_CHARS_HE = 2500  # иврит токенизируется плотнее: запас до лимита модели
GONE = re.compile(r"^[\s(\[]*(weggefallen|aufgehoben|entfällt|בוטל|בוטלו|נמחק|נמחקה|נמחקו|-)[\s)\].]*$", re.I)


def slugify(url: str) -> str:
    m = re.search(r"/([^/]+)/xml\.zip$", url)
    return m.group(1) if m else re.sub(r"[^\w\-]", "_", url)


def element_text(el) -> str:
    parts = []
    if el.text:
        parts.append(el.text.strip())
    for child in el:
        parts.append(element_text(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)


def standangabe_text(meta) -> list[str]:
    notes = []
    for st in meta.findall("standangabe"):
        note = " ".join(p.strip() for p in (st.findtext("standtyp") or "", st.findtext("standkommentar") or "") if p.strip())
        if note:
            notes.append(note)
    return notes


def parse_law_xml(xml_bytes: bytes, slug: str, source_url: str) -> dict:
    root = ET.fromstring(xml_bytes)
    short = long_title = ausf = None
    law_stand: list[str] = []
    sections = []
    for norm in root.iter("norm"):
        meta = norm.find("metadaten")
        enbez = titel = None
        section_stand: list[str] = []
        if meta is not None:
            jurabk, kurzue, langue = meta.findtext("jurabk"), meta.findtext("kurzue"), meta.findtext("langue")
            enbez, titel = meta.findtext("enbez"), meta.findtext("titel")
            section_stand = standangabe_text(meta)
            if short is None and jurabk:
                short = jurabk.strip()
            if long_title is None and (langue or kurzue):
                long_title = (langue or kurzue).strip()
            if ausf is None and meta.findtext("ausfertigung-datum"):
                ausf = meta.findtext("ausfertigung-datum").strip()
            if not law_stand and section_stand:
                law_stand = section_stand
        text_parts = []
        textdaten = norm.find("textdaten")
        if textdaten is not None:
            for tag in ("Content", "Content.Block"):
                for content in textdaten.iter(tag):
                    txt = element_text(content)
                    if txt:
                        text_parts.append(txt)
        text = "\n".join(text_parts).strip()
        if not text and not enbez:
            continue
        sections.append({"enbez": (enbez or "").strip(), "titel": (titel or "").strip(), "text": text,
                         "stand": section_stand})
    return {"slug": slug, "source_url": source_url.replace("/xml.zip", "/"), "law_abbreviation": short or slug,
            "law_title": long_title or "", "ausfertigung_datum": ausf or "", "stand": law_stand, "sections": sections}


def merge_parsed(slug: str, zip_url: str, parsed_files: list[dict]) -> dict:
    """Несколько XML в одном zip сливаются в один закон (как в старом сервисе)."""
    abbreviation, title, stand, sections = slug, "", [], []
    for parsed in parsed_files:
        title = parsed["law_title"] or title
        abbreviation = parsed["law_abbreviation"] or abbreviation
        stand = parsed["stand"] or stand
        sections.extend(parsed["sections"])
    return {"slug": slug, "law_abbreviation": abbreviation, "law_title": title, "category": category_for(slug),
            "stand": stand, "source_url": zip_url.replace("/xml.zip", "/"), "sections": sections}


def is_gone(text: str) -> bool:
    """Отменённые параграфы («weggefallen», «-») в поиске не нужны."""
    return not text.strip() or bool(GONE.match(text.strip()))


def chunk_id(slug: str, enbez: str, idx: int, part: int = 0) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}::{enbez}::{idx}::{part}"))


def split_long_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts, current, current_len = [], [], 0
    for para in text.split("\n"):
        piece = para + "\n"
        if current_len + len(piece) > max_chars and current:
            parts.append("".join(current))
            current, current_len = [], 0
        if len(piece) > max_chars:
            parts.extend(piece[i:i + max_chars] for i in range(0, len(piece), max_chars))
            continue
        current.append(piece)
        current_len += len(piece)
    if current:
        parts.append("".join(current))
    return parts


def chunks_of(law: dict):
    """Все актуальные куски закона: (id, enbez, titel, part, text, sha256).

    Если у статьи есть `context` (IL: «закон · глава · статья – заголовок»), он ставится перед текстом куска: в статье
    часто не названо, о чём закон, и вектор без этого теряет смысл."""
    limit = MAX_CHARS_HE if law.get("jurisdiction") == "IL" else MAX_CHARS
    for idx, section in enumerate(law["sections"]):
        text = (section.get("text") or "").strip()
        if is_gone(text):
            continue
        context = (section.get("context") or "").strip()
        pieces = split_long_text(text, limit - len(context) - 1 if context else limit)
        for part, piece in enumerate(pieces):
            label = f"{part + 1}/{len(pieces)}" if len(pieces) > 1 else ""
            body = f"{context}\n{piece}" if context else piece
            yield (chunk_id(law["slug"], section["enbez"], idx, part), section["enbez"], section["titel"], label,
                   body, hashlib.sha256(body.encode("utf-8")).hexdigest())
