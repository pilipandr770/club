def safe_next(target):
    """Разрешает только внутренние пути: защита от открытого перенаправления."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None
