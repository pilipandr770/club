"""Встроенные инструменты клуба (тариф «Starter» и выше): проверки сайта, поиск по закону, панель персон.

Каждый запуск пишется в ToolRun; он же считается для месячного лимита по тарифу (config/plans.py → TOOL_LIMITS).
Неудавшиеся проверки (сайт недоступен и т. п.) не сохраняются и лимит не тратят.
"""

from dataclasses import dataclass

from flask_babel import lazy_gettext as _l

from ...extensions import db
from ...models import ToolRun
from ...utils import utcnow
from ..audit import audit
from ..entitlements import tool_limit
from . import compliance, laws, panel, security, seo
from .common import ToolError

__all__ = ["TOOLS", "ToolError", "LimitReached", "run_laws", "run_panel", "run_site_tool", "spec_of", "tool_state"]


@dataclass(frozen=True)
class ToolSpec:
    key: str
    title: object
    summary: object
    kind: str  # site | domain | laws | panel


TOOLS = (
    ToolSpec("seo", _l("SEO check"), _l("Checks a public page technically and by content and lists what to fix first."), "site"),
    ToolSpec("compliance", _l("Compliance check"),
             _l("Looks for legal notice, privacy policy, cookie consent and trackers on your website."), "site"),
    ToolSpec("security", _l("Security check"),
             _l("Passive check of encryption, security headers, cookies and e-mail protection of your domain."), "domain"),
    ToolSpec("laws", _l("Law search"),
             _l("Finds German federal law provisions, quotes them verbatim and explains what they say."), "laws"),
    ToolSpec("panel", _l("Market panel"),
             _l("Up to 100 fictional personas rate your product idea before you launch."), "panel"),
)
_BY_KEY = {tool.key: tool for tool in TOOLS}


class LimitReached(Exception):
    pass


def spec_of(key: str):
    return _BY_KEY.get(key)


def used_this_month(user_id: int, key: str) -> int:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return ToolRun.query.filter(ToolRun.user_id == user_id, ToolRun.tool == key, ToolRun.created_at >= start,
                                ToolRun.status != "failed").count()


def tool_state(user, key: str) -> dict:
    limit = tool_limit(user, key)
    used = used_this_month(user.id, key)
    return {"limit": limit, "used": used, "left": max(0, limit - used)}


def _check_limit(user, key: str) -> None:
    state = tool_state(user, key)
    if state["limit"] == 0 or state["left"] <= 0:
        raise LimitReached(key)


def _save(user, key: str, subject: str, result: dict, report: dict) -> ToolRun:
    run = ToolRun(user_id=user.id, tool=key, subject=subject[:500], status="done", result=result, report=report)
    db.session.add(run)
    db.session.flush()
    audit("tool.run", target=f"{key}:{run.id}", actor_id=user.id)
    return run


def run_site_tool(user, key: str, raw: str, keywords_raw: str = "") -> ToolRun:
    """seo | compliance | security. Исключения: LimitReached, ToolError (текст для пользователя)."""
    _check_limit(user, key)
    if key == "seo":
        keywords = [part.strip() for part in keywords_raw.replace("\n", ",").split(",") if part.strip()][:5]
        result = seo.analyze(raw, keywords)
        report = seo.build_report(result, keywords)
        subject = result["url"]
    elif key == "compliance":
        result = compliance.analyze(raw)
        report = compliance.build_report(result)
        subject = result["url"]
    elif key == "security":
        result = security.analyze(raw)
        report = security.build_report(result)
        subject = result["url"]
    else:
        raise ValueError(key)
    return _save(user, key, subject, result, report)


def run_laws(user, query: str, category: str) -> ToolRun:
    _check_limit(user, "laws")
    hits = laws.search(query, category)
    explanation = laws.explain(query, hits)
    return _save(user, "laws", query, {"hits": hits, "category": category}, explanation)


def run_panel(user, form: dict) -> ToolRun:
    """form: product_name, description, audience, regional, unit, price_a, price_b, n (уже проверены вызывающим)."""
    _check_limit(user, "panel")
    run = panel.start_run(user, form)
    audit("tool.run", target=f"panel:{run.id}", actor_id=user.id)
    db.session.commit()
    return run
