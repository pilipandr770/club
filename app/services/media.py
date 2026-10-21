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


# --------------------------------------------------------------------------- PDF-презентации
MAX_PDF_PAGES = 60
DECK_TEXT_CHARS = 30000


def process_pdf(raw: bytes, max_bytes: int) -> tuple[bytes, str]:
    """Проверяет PDF, пересобирает его из страниц и вынимает текст. Возвращает (байты, текст). Бросает MediaError.

    Пересборка отбрасывает вложения, скрипты уровня документа и действия при открытии. Файл всё равно отдаётся
    только как скачивание, не в окне браузера.
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PyPdfError

    if not raw:
        raise MediaError("empty")
    if len(raw) > max_bytes:
        raise MediaError("too_large")
    if raw.lstrip()[:5] != b"%PDF-":
        raise MediaError("not_a_pdf")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise MediaError("encrypted")
        if len(reader.pages) == 0:
            raise MediaError("not_a_pdf")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise MediaError("too_many_pages")
        writer = PdfWriter()
        parts = []
        for page in reader.pages:
            writer.add_page(page)
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - одна «плохая» страница не должна ронять загрузку
                parts.append("")
        out = io.BytesIO()
        writer.write(out)
    except MediaError:
        raise
    except (PyPdfError, ValueError, OSError, KeyError, TypeError, RecursionError):
        raise MediaError("not_a_pdf") from None
    text = " ".join(" ".join(parts).split())[:DECK_TEXT_CHARS]
    return out.getvalue(), text


def startup_file_key(startup_id: int, kind: str, ext: str) -> str:
    return f"startups/{startup_id}/{kind}-{uuid.uuid4().hex}.{ext}"


def attach_files(obj, storage, prefix: str, image, doc, remove_image=False, remove_doc=False, *,
                 attrs: tuple[str, str, str], image_max_bytes: int, doc_max_bytes: int) -> list[str]:
    """Сохраняет картинку и PDF для любой записи (стартап, поездка). Бросает MediaError до записи в хранилище.

    attrs — имена полей записи: (ключ картинки, ключ PDF, текст PDF). prefix — каталог, например "trips/5".
    Возвращает ключи старых файлов: их стирают после успешного commit.
    """
    image_attr, doc_attr, text_attr = attrs
    new_image = new_doc = None
    if image is not None and image.filename:
        data, ext, _mime = process_image(image.read(image_max_bytes + 1), image_max_bytes)
        new_image = (f"{prefix}/image-{uuid.uuid4().hex}.{ext}", data)
    if doc is not None and doc.filename:
        data, text = process_pdf(doc.read(doc_max_bytes + 1), doc_max_bytes)
        new_doc = (f"{prefix}/doc-{uuid.uuid4().hex}.pdf", data, text)

    obsolete = []
    if new_image:
        storage.save(*new_image)
        obsolete.append(getattr(obj, image_attr))
        setattr(obj, image_attr, new_image[0])
    elif remove_image and getattr(obj, image_attr):
        obsolete.append(getattr(obj, image_attr))
        setattr(obj, image_attr, None)
    if new_doc:
        storage.save(new_doc[0], new_doc[1])
        obsolete.append(getattr(obj, doc_attr))
        setattr(obj, doc_attr, new_doc[0])
        setattr(obj, text_attr, new_doc[2])
    elif remove_doc and getattr(obj, doc_attr):
        obsolete.append(getattr(obj, doc_attr))
        setattr(obj, doc_attr, None)
        setattr(obj, text_attr, "")
    return [key for key in obsolete if key]
