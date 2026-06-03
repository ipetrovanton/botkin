"""Подготовка PDF/изображений к VLM: разрешение, контраст/резкость, выпрямление наклона.

Размер изображения определяет число vision-токенов и качество чтения. Маленькие
Telegram-фото апскейлятся до целевого разрешения; крупные — ужимаются. Контраст/резкость
повышаются (CLAHE + unsharp), наклон фото выпрямляется best-effort (с фолбэком на полный кадр).
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover — окружение без pillow-heif
    pass

from botkin.config import (
    IMAGE_CLAHE_CLIP,
    IMAGE_DESKEW_MAX_AREA,
    IMAGE_DESKEW_MIN_ANGLE,
    IMAGE_DESKEW_MIN_AREA,
    IMAGE_EXTRACT_LONG_SIDE,
    IMAGE_JPEG_QUALITY,
    IMAGE_UNSHARP_AMOUNT,
    MAX_PAGES,
    PDF_RENDER_DPI,
)

log = logging.getLogger(__name__)


def _resize(img: Image.Image, long_side: int, upscale: bool) -> Image.Image:
    """Приводит длинную сторону к long_side. upscale=True — растит маленькие тоже."""
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    width, height = img.size
    longest = max(width, height)
    if longest > long_side or (upscale and longest < long_side):
        ratio = long_side / longest
        resample = Image.LANCZOS if ratio < 1 else Image.BICUBIC
        img = img.resize((round(width * ratio), round(height * ratio)), resample)
    return img


def _enhance(arr: np.ndarray) -> np.ndarray:
    """CLAHE-контраст по L-каналу + мягкий unsharp. Вход/выход — RGB uint8."""
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    luminance, a, b = cv2.split(lab)
    luminance = cv2.createCLAHE(clipLimit=IMAGE_CLAHE_CLIP, tileGridSize=(8, 8)).apply(luminance)
    out = cv2.cvtColor(cv2.merge((luminance, a, b)), cv2.COLOR_LAB2RGB)
    blur = cv2.GaussianBlur(out, (0, 0), 3)
    return cv2.addWeighted(out, IMAGE_UNSHARP_AMOUNT, blur, 1.0 - IMAGE_UNSHARP_AMOUNT, 0)


def _doc_angle(arr: np.ndarray) -> float | None:
    """Угол наклона документа [-45,45]°, если он надёжно определён, иначе None.

    Документ — светлая (низкая насыщенность) область на более насыщенном фоне.
    """
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    _, mask = cv2.threshold(saturation, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((35, 35), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area_fraction = cv2.contourArea(contour) / (arr.shape[0] * arr.shape[1])
    if not (IMAGE_DESKEW_MIN_AREA <= area_fraction <= IMAGE_DESKEW_MAX_AREA):
        return None
    angle = cv2.minAreaRect(contour)[2]
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    if abs(angle) < IMAGE_DESKEW_MIN_ANGLE:
        return None
    return float(angle)


def _deskew(arr: np.ndarray) -> np.ndarray:
    """Выпрямляет наклон, если он надёжно определён; иначе возвращает как есть."""
    angle = _doc_angle(arr)
    if angle is None:
        return arr
    height, width = arr.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        arr, matrix, (width, height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def _encode_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buf.getvalue()


def _process(img: Image.Image, long_side: int, upscale: bool, deskew: bool, enhance: bool) -> bytes:
    if deskew:
        img = Image.fromarray(_deskew(np.asarray(img.convert("RGB"))))
    img = _resize(img, long_side, upscale)
    if enhance:
        img = Image.fromarray(_enhance(np.asarray(img)))
    return _encode_jpeg(img)


def _pdf_pages(path: Path, long_side: int, upscale: bool, enhance: bool) -> list[bytes]:
    out: list[bytes] = []
    doc = pymupdf.open(str(path))
    try:
        log.info("[PDF] %s: страниц в документе=%d | рендер до %d @ %d dpi",
                 path.name, doc.page_count, MAX_PAGES, PDF_RENDER_DPI)
        for index, page in enumerate(doc):
            if index >= MAX_PAGES:
                break
            pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            jpeg = _process(img, long_side, upscale, deskew=False, enhance=enhance)
            fw, fh = Image.open(io.BytesIO(jpeg)).size
            log.info("[PDF] %s стр.%d: рендер %dx%d → итог %dx%d px, JPEG %d КБ",
                     path.name, index + 1, pix.width, pix.height, fw, fh, len(jpeg) // 1024)
            out.append(jpeg)
    finally:
        doc.close()
    return out


def prepare_images(
    file_path: Path | str,
    long_side: int | None = None,
    upscale: bool = False,
    deskew: bool = False,
    enhance: bool = False,
) -> list[bytes]:
    """PDF/изображение → список JPEG-байтов. deskew применяется только к растровым фото."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    limit = long_side or IMAGE_EXTRACT_LONG_SIDE
    if path.suffix.lower() == ".pdf":
        return _pdf_pages(path, limit, upscale, enhance)

    with Image.open(path) as img:
        jpeg = _process(img, limit, upscale, deskew, enhance)
    fw, fh = Image.open(io.BytesIO(jpeg)).size
    log.info("[IMG] %s: итог %dx%d px, JPEG %d КБ", path.name, fw, fh, len(jpeg) // 1024)
    return [jpeg]


def to_base64_jpegs(images: list[bytes]) -> list[str]:
    return [base64.b64encode(raw).decode("utf-8") for raw in images]
