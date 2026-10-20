"""Права доступа по тарифам. Любая функция проверяется здесь, а не условиями в шаблонах (ARCHITECTURE.md §1.4)."""

from functools import wraps

from flask import flash, redirect, request, url_for
from flask_babel import gettext as _
from flask_login import current_user

from config.plans import DEFAULT_PLAN, PLANS, TOOL_LIMITS


def plan_features(user) -> frozenset:
    return PLANS.get(user.plan or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])["features"]


def has_feature(user, feature: str) -> bool:
    """Без проверки личности доступа нет ни к одной функции, даже к гостевой."""
    if user is None or not user.is_authenticated or not user.is_verified:
        return False
    return feature in plan_features(user)


def tool_limit(user, tool: str) -> int:
    """Запусков инструмента в месяц по тарифу; 0 — тариф инструмент не включает."""
    return TOOL_LIMITS.get(user.plan or DEFAULT_PLAN, {}).get(tool, 0)


def requires_verified(view):
    """Пускает только участников с пройденной проверкой личности."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        if not current_user.is_verified:
            flash(_("This area opens after your identity check."), "info")
            return redirect(url_for("member.dashboard"))
        return view(*args, **kwargs)

    return wrapper


def requires_feature(feature: str):
    """Пускает только участников, у которых тариф включает функцию (и пройдена проверка личности)."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.path))
            if not current_user.is_verified:
                flash(_("This area opens after your identity check."), "info")
                return redirect(url_for("member.dashboard"))
            if not has_feature(current_user, feature):
                flash(_("This section is not included in your plan."), "info")
                return redirect(url_for("member.dashboard"))
            return view(*args, **kwargs)

        return wrapper

    return decorator

