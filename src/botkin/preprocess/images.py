"""Подготовка PDF/изображений к VLM: контролируемое разрешение и JPEG-качество.

Размер изображения напрямую определяет число vision-токенов и латентность,
поэтому длинная сторона приводится к разумному пределу (см. config.IMAGE_MAX_LONG_SIDE).
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

# Регистрируем HEIC-плагин для Pillow (фото с iPhone).
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover — окружение без pillow-heif
    pass

from botkin.config import (
    IMAGE_JPEG_QUALITY,
    IMAGE_MAX_LONG_SIDE,
    MAX_PAGES,
    PDF_RENDER_DPI,
)


def _downscale(img: Image.Image, long_side: int) -> Image.Image:
    img = ImageOps.exif_transpose(img)   # учёт ориентации из EXIF (фото с телефона)
    if img.mode != "RGB":
        img = img.convert("RGB")
    width, height = img.size
    longest = max(width, height)
    if longest > long_side:
        ratio = long_side / longest
        img = img.resize((round(width * ratio), round(height * ratio)), Image.LANCZOS)
    return img


def _encode_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buf.getvalue()


def _pdf_pages(path: Path, long_side: int) -> list[bytes]:
    out: list[bytes] = []
    doc = pymupdf.open(str(path))
    try:
        for index, page in enumerate(doc):
            if index >= MAX_PAGES:
                break
            pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            out.append(_encode_jpeg(_downscale(img, long_side)))
    finally:
        doc.close()
    return out


def prepare_images(file_path: Path | str, long_side: int | None = None) -> list[bytes]:
    """PDF/изображение → список JPEG-байтов с контролируемым разрешением.

    long_side по умолчанию = IMAGE_MAX_LONG_SIDE; для дешёвой классификации передаётся
    меньшее значение (IMAGE_CLASSIFY_LONG_SIDE).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    limit = long_side or IMAGE_MAX_LONG_SIDE
    if path.suffix.lower() == ".pdf":
        return _pdf_pages(path, limit)

    with Image.open(path) as img:
        return [_encode_jpeg(_downscale(img, limit))]


def to_base64_jpegs(images: list[bytes]) -> list[str]:
    """Кодирует JPEG-байты в base64-строки для data-url."""
    return [base64.b64encode(raw).decode("utf-8") for raw in images]
