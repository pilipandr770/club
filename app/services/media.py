"""Загрузка фото: проверка, очистка и хранение за интерфейсом.

Все изображения пересохраняются заново: это убирает EXIF с геолокацией и скрытые данные,
а неподдерживаемые или подделанные файлы не проходят проверку. Хранилище сейчас локальное;
при деплое добавим S3-совместимое (Cloudflare R2), интерфейс остаётся тем же.
"""

import io
import os
import tempfile
import uuid

from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
MAX_SIDE = 1024
MAX_PIXELS = 24_000_000  # защита от «бомб» с огромным разрешением


class MediaError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class LocalStorage:
    def __init__(self, root):
        self.root = os.path.abspath(root)

    def _path(self, key):
        # ключи генерирует сервер; дополнительно не даём выйти за корень
        path = os.path.abspath(os.path.join(self.root, key))
        if not path.startswith(self.root + os.sep):
            raise MediaError("bad_key")
        return path

    def save(self, key, data: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)  # атомарная замена

    def read(self, key) -> bytes | None:
        try:
            with open(self._path(key), "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def delete(self, key) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass


def process_image(raw: bytes, max_bytes: int) -> tuple[bytes, str, str]:
    """Проверяет и очищает изображение. Возвращает (байты, расширение, mime-тип). Бросает MediaError."""
    if not raw:
        raise MediaError("empty")
    if len(raw) > max_bytes:
        raise MediaError("too_large")
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()  # ловит повреждённые файлы
        image = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise MediaError("not_an_image") from None

    fmt = image.format
    if fmt not in ALLOWED_FORMATS:
        raise MediaError("bad_format")
    if image.width * image.height > MAX_PIXELS:
        raise MediaError("too_large_pixels")

    image.thumbnail((MAX_SIDE, MAX_SIDE))
    if fmt == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if fmt == "PNG" and image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        image = image.convert("RGBA")

    out = io.BytesIO()
    save_format = fmt
    if fmt == "JPEG":
        image.convert("RGB").save(out, format="JPEG", quality=88, optimize=True)
    elif fmt == "PNG":
        image.save(out, format="PNG", optimize=True)
    else:
        image.save(out, format="WEBP", quality=88)
    ext, mime = ALLOWED_FORMATS[save_format]
    return out.getvalue(), ext, mime


def profile_photo_key(user_id: int, ext: str) -> str:
    return f"avatars/{user_id}/{uuid.uuid4().hex}.{ext}"
