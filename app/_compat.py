def safe_next(target):
    """Разрешает только внутренние пути: защита от открытого перенаправления."""
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(char) < 32 or ord(char) == 127 for char in target)):
        return target
    return None
