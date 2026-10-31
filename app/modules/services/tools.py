"""Встроенные инструменты: форма, запуск, сохранённый отчёт. Доступ — как у раздела услуг (services.use)."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from ...extensions import db
from ...jurisdictions import get_pack
from ...models import ToolRun
from ...services.entitlements import requires_feature
from ...services.tools import (TOOLS, LimitReached, ToolError, run_laws, run_panel, run_site_tool, spec_of,
                               tool_state)
from ...services.laws import search as laws_corpus
from ...services.tools import laws as laws_tool
from ...services.tools import panel as panel_tool
from . import bp

PANEL_SIZES = (20, 50, 100)
AUDIENCES = ("beide", "privat", "business")
REGIONS = ("de", "rhein-main")


def _panel_form(form):
    """Проверка формы панели. Возвращает (данные, словарь ошибок)."""
    errors = {}
    name = form.get("product_name", "").strip()[:120]
    description = form.get("description", "").strip()[:2000]
    if not name:
        errors["product_name"] = _("Please name your product.")
    if len(description) < 40:
        errors["description"] = _("Please describe the product in at least 40 characters.")
    prices = []
    for field in ("price_a", "price_b"):
        raw = form.get(field, "").strip()
        value = panel_tool._num(raw) if raw else None
        if raw and (value is None or value <= 0 or value >= 1_000_000):
            errors[field] = _("Please enter a price in euros, for example 29,90.")
        prices.append(value)
    if prices[1] and not prices[0]:
        errors["price_a"] = _("Price B needs price A.")
    try:
        n = int(form.get("n", "100"))
    except ValueError:
        n = 100
    unit = form.get("unit") if form.get("unit") in panel_tool.UNITS else "einmalig"
    audience = form.get("audience") if form.get("audience") in AUDIENCES else "beide"
    regional = form.get("regional") if form.get("regional") in REGIONS else "de"
    data = {"product_name": name, "description": description, "audience": audience, "regional": regional,
            "unit": unit, "price_a": prices[0], "price_b": prices[1], "n": n if n in PANEL_SIZES else 100}
    return data, errors


def _laws_context():
    """Länder mit geladenem Korpus, ihre Kategorien und der Korpus-Überblick (Gesetze samt Quelle und Revision)."""
    codes = laws_tool.available_jurisdictions()
    packs = [get_pack(code) for code in codes]
    return {"packs": packs, "corpus": {code: laws_corpus.corpus_laws(code) for code in codes}}


def _limit_message():
    return _("You have used all runs of this tool for this month. They renew at the start of next month.")


@bp.route("/tools/")
@requires_feature("services.use")
def tools_index():
    states = {tool.key: tool_state(current_user, tool.key) for tool in TOOLS}
    runs = (ToolRun.query.filter(ToolRun.user_id == current_user.id, ToolRun.tool != "assistant").order_by(ToolRun.created_at.desc()).limit(10).all())
    return render_template("modules/tools_index.html", tools=TOOLS, states=states, runs=runs,
                           laws_enabled=laws_tool.enabled())


@bp.route("/tools/<key>", methods=["GET", "POST"])
@requires_feature("services.use")
def tool_page(key):
    spec = spec_of(key)
    if spec is None or (key == "laws" and not laws_tool.enabled()):
        abort(404)
    state = tool_state(current_user, key)
    form, errors = request.form if request.method == "POST" else {}, {}
    if request.method == "POST":
        try:
            if spec.kind in ("site", "domain"):
                run = run_site_tool(current_user, key, request.form.get("target", ""), request.form.get("keywords", ""))
            elif spec.kind == "laws":
                jurisdiction = request.form.get("jurisdiction", "DE")
                if jurisdiction not in laws_tool.available_jurisdictions():
                    raise ToolError(_("There is no law collection for this country."))
                run = run_laws(current_user, request.form.get("query", ""), request.form.get("category", ""), jurisdiction)
            else:
                data, errors = _panel_form(request.form)
                if errors:
                    return render_template("modules/tool_form.html", spec=spec, state=state, form=form, errors=errors,
                                           categories=laws_tool.CATEGORIES, units=panel_tool.UNITS, sizes=PANEL_SIZES)
                run = run_panel(current_user, data)
            db.session.commit()
            return redirect(url_for("services.tool_run", run_id=run.id))
        except LimitReached:
            flash(_limit_message(), "error")
        except ToolError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("modules/tool_form.html", spec=spec, state=state, form=form, errors=errors,
                           categories=laws_tool.CATEGORIES, units=panel_tool.UNITS, sizes=PANEL_SIZES,
                           laws=_laws_context() if spec.kind == "laws" else None)


@bp.route("/tools/run/<int:run_id>")
@requires_feature("services.use")
def tool_run(run_id):
    run = ToolRun.query.get_or_404(run_id)
    if run.user_id != current_user.id:
        abort(404)
    spec = spec_of(run.tool) or abort(404)
    if run.tool == "panel":
        panel_tool.refresh_status(run)
    result = run.result or {}
    pack = get_pack(result.get("jurisdiction", "DE")) if run.tool == "laws" else None
    return render_template(f"modules/tool_report_{_report_kind(run.tool)}.html", run=run, spec=spec,
                           result=result, report=run.report or {}, units=panel_tool.UNITS,
                           intent_label=panel_tool.INTENT_LABEL, pack=pack)


def _report_kind(tool):
    return {"seo": "findings", "compliance": "findings", "security": "findings", "laws": "laws",
            "panel": "panel"}[tool]
