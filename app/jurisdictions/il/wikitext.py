"""Разбор ספר החוקים הפתוח (Hebrew Wikisource): вики-разметка с шаблонами «ח:» → закон со статьями.

Разметка уже структурная, поэтому модель для разбора не нужна:
  {{ח:כותרת|название}} · {{ח:תיבה|ссылка на ס״ח|имя|url}} в шапке (поправки)
  {{ח:קטע1..4|id|название}} разделы · {{ח:סעיף|номер|заголовок|תיקון: годы}} статья
  {{ח:ת}} абзац · {{ח:תת|(א)}} {{ח:תתת|(1)}} … подпункты · {{ח:הערה|…}} сноска (в текст не берём)
  {{ח:פנימי|цель|текст}} / {{ח:חיצוני|цель|текст}} ссылки (берём текст) · {{ח:סוף}} конец закона.
Текст статьи отдаём дословно из вики; сноски и ссылки на Кнессет остаются только в метаданных.
"""

import html
import re

TOC_TITLE = "תוכן עניינים"
ANNEX_PREFIXES = ("תוספת", "לוח", "טופס")
MARKERS = {"ח:סעיף", "ח:סעיף*", "ח:קטע1", "ח:קטע2", "ח:קטע3", "ח:קטע4", "ח:מבוא", "ח:חתימות", "ח:כותרת"}
_LEVEL_TAG = re.compile(r"^ח:(ת+)$")
_NAMED_ARG = re.compile(r"^[^\s=|:]+=")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_REF = re.compile(r"<ref[^>]*?(?:/>|>.*?</ref>)", re.S)
_BR = re.compile(r"<br\s*/?>", re.I)
_LINK = re.compile(r"\[\[([^\]|]*)\|?([^\]]*)\]\]")
_EXT_LINK = re.compile(r"\[(?:https?://\S+)\s+([^\]]+)\]")
_BOLD = re.compile(r"'{2,5}")


def _balanced_end(text: str, start: int) -> int:
    """Индекс сразу после «}}», закрывающей «{{» в позиции start; -1, если пара не найдена."""
    depth, i, n = 0, start, len(text)
    while i < n - 1:
        pair = text[i:i + 2]
        if pair == "{{":
            depth += 1
            i += 2
        elif pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return -1


def _split_args(inner: str) -> list[str]:
    """Аргументы шаблона по «|» верхнего уровня (внутри {{ }} и [[ ]] не режем)."""
    args, depth, buf, i, n = [], 0, [], 0, len(inner)
    while i < n:
        pair = inner[i:i + 2]
        if pair in ("{{", "[["):
            depth += 1
            buf.append(pair)
            i += 2
        elif pair in ("}}", "]]"):
            depth -= 1
            buf.append(pair)
            i += 2
        elif inner[i] == "|" and depth == 0:
            args.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(inner[i])
            i += 1
    args.append("".join(buf))
    return args


def top_level_templates(text: str):
    """(имя, аргументы, начало, конец) для шаблонов верхнего уровня, по порядку."""
    out, i = [], 0
    while True:
        start = text.find("{{", i)
        if start < 0:
            return out
        end = _balanced_end(text, start)
        if end < 0:
            return out
        parts = _split_args(text[start + 2:end - 2])
        out.append((parts[0].strip(), parts[1:], start, end))
        i = end


def render(text: str) -> str:
    """Шаблоны → обычный текст; абзацы и подпункты — с новой строки."""
    out, i = [], 0
    while i < len(text):
        start = text.find("{{", i)
        if start < 0:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = _balanced_end(text, start)
        if end < 0:
            out.append(text[start:])
            break
        parts = _split_args(text[start + 2:end - 2])
        out.append(_render_template(parts[0].strip(), parts[1:]))
        i = end
    return "".join(out)


def _render_template(name: str, args: list[str]) -> str:
    if name in ("ח:פנימי", "ח:חיצוני"):
        return render(args[-1] if len(args) >= 2 else (args[0] if args else ""))
    match = _LEVEL_TAG.match(name)
    if match:
        label = render(args[0]).strip() if args else ""
        indent = "  " * (len(match.group(1)) - 1)
        return f"\n{indent}{label} " if label else f"\n{indent}"
    if name == "מוקטן":
        return render(args[0]) if args else ""
    if name == "ש":
        return "\n"
    if name == "=":
        return "="
    if name == "דוכיווני":
        return " ".join(render(a).strip() for a in args)
    if name.startswith("ח:"):
        return ""  # сноски, рамки с поправками, разделители, подписи — не часть текста статьи
    return " ".join(render(a).strip() for a in args if not _NAMED_ARG.match(a.strip()))


def clean(text: str) -> str:
    """HTML и вики-остатки → простой текст с абзацами."""
    text = _COMMENT.sub("", text)
    text = _REF.sub("", text)
    text = _BR.sub("\n", text)
    text = _TAG.sub("", text)
    text = _LINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = _EXT_LINK.sub(r"\1", text)
    text = _BOLD.sub("", text)
    text = _table_to_text(text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = []
    for line in text.split("\n"):
        indent = "  " * ((len(line) - len(line.lstrip(" "))) // 2) if line.startswith("  ") else ""
        lines.append(indent + re.sub(r"[ \t]+", " ", line.strip()))
    text = "\n".join(lines)
    text = re.sub(r"\n\s*\n+", "\n", text)
    lines = text.strip("\n").split("\n")
    shift = min((len(line) - len(line.lstrip(" ")) for line in lines if line.strip()), default=0)
    return "\n".join(line[shift:] for line in lines)  # верхний уровень без отступа, даже если статья начинается с подпункта


def _table_to_text(text: str) -> str:
    if "{|" not in text:
        return text
    rows = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("{|", "|}", "|-", "|+")):
            if stripped.startswith("|-") or stripped.startswith("|}"):
                rows.append("")
            continue
        if stripped.startswith(("|", "!")):
            cells = re.split(r"\|\||!!", stripped[1:])
            rows.append(" | ".join(c.split("|", 1)[-1].strip() if "|" in c and "=" in c.split("|", 1)[0] else c.strip()
                                   for c in cells))
        else:
            rows.append(line)
    return "\n".join(rows)


def _arg(args: list[str], index: int) -> str:
    positional = [a for a in args if not _NAMED_ARG.match(a.strip())]
    return clean(render(positional[index])).replace("\n", " ").strip() if index < len(positional) else ""


def parse_law(wikitext: str, slug: str, page: str, revid: int, timestamp: str, category: str) -> dict:
    """Закон в формате `services/laws/sync.store_law` (jurisdiction=IL)."""
    body = wikitext.split("{{ח:סוף}}", 1)[0]
    found = top_level_templates(body)
    marks = [t for t in found if t[0] in MARKERS]
    body_start = next((t[2] for t in marks if t[0] not in ("ח:כותרת", "ח:חתימות")), len(body))
    amendments = [f"{_arg(t[1], 0)} — {_arg(t[1], 1)}" for t in found if t[0] == "ח:תיבה" and t[2] < body_start
                  and len(t[1]) >= 2]
    title, sections = "", []
    chapters: dict[int, str] = {}
    current: dict | None = None
    annex = ""

    def start_section(enbez, titel, amend, segment):
        nonlocal current
        current = {"enbez": enbez, "titel": titel, "amend": amend, "raw": [segment],
                   "chapter": " · ".join(chapters[k] for k in sorted(chapters) if chapters[k])}
        sections.append(current)

    for index, (name, args, start, end) in enumerate(marks):
        next_start = marks[index + 1][2] if index + 1 < len(marks) else len(body)
        segment = body[end:next_start]
        if name == "ח:כותרת":
            title = _arg(args, 0)
            current = None
        elif name in ("ח:קטע1", "ח:קטע2", "ח:קטע3", "ח:קטע4"):
            level = int(name[-1])
            heading = _arg(args, 1)
            current = None
            if heading == TOC_TITLE:
                continue
            for deeper in [k for k in chapters if k >= level]:
                del chapters[deeper]
            if heading.startswith(ANNEX_PREFIXES):
                annex = heading
                start_section(heading, "", "", segment)
            elif heading:
                chapters[level] = heading
                annex = ""
        elif name == "ח:סעיף":
            number = _arg(args, 0)
            start_section(f"סעיף {number}" if number else "", _arg(args, 1), _arg(args, 2), segment)
        elif name == "ח:סעיף*":
            # Пункт списка (перечень в статье или в приложении), а не отдельная статья: дописываем к текущему блоку
            label, heading = _arg(args, 0), _arg(args, 1)
            item = f"\n{label} {heading} {segment}".replace("  ", " ")
            if current is not None:
                current["raw"].append(item)
            elif annex:
                start_section(annex, "", "", item)
        elif name == "ח:מבוא":
            start_section("מבוא", "", "", segment)
        elif name == "ח:חתימות":
            current = None
        # прочие сегменты (оглавление, подписи) отбрасываем

    short = re.split(r",\s*", title, maxsplit=1)[0].strip() if title else page
    out_sections = []
    for section in sections:
        text = clean(render("".join(section["raw"])))
        context = " · ".join(p for p in (short, section["chapter"],
                                         (section["enbez"] + (f" – {section['titel']}" if section["titel"] else ""))) if p)
        out_sections.append({"enbez": section["enbez"], "titel": section["titel"], "text": text,
                             "stand": [section["amend"]] if section["amend"] else [], "context": context})
    stand = [f"Open Law Book, {timestamp[:10]} (revision {revid})"]
    if amendments:
        stand.append(f"{amendments[0]}")
        if len(amendments) > 1:
            stand.append(f"{amendments[-1]}")
    return {"slug": slug, "jurisdiction": "IL", "law_abbreviation": short[:120], "law_title": (title or page)[:400],
            "category": category, "stand": stand, "revision": str(revid),
            "source_url": f"https://he.wikisource.org/w/index.php?title={_quote(page)}&oldid={revid}",
            "sections": out_sections}


def _quote(page: str) -> str:
    from urllib.parse import quote
    return quote(page.replace(" ", "_"), safe="")
