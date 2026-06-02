"""Определение формата загруженного файла по содержимому (magic-байты).

iPhone отправляет HEIC файлом часто без расширения или как .heif — поэтому полагаемся
на содержимое, а имя файла используем лишь как подсказку.
"""
from __future__ import annotations

from pathlib import Path

# Бренды ISO-BMFF (box ftyp) для HEIF/HEIC.
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"heif"}


def sniff_extension(head: bytes) -> str | None:
    """Каноничное расширение по первым байтам файла или None."""
    if head[:4] == b"%PDF":
        return ".pdf"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return ".heic"
    return None


def resolve_extension(filename: str | None, head: bytes, allowed: set[str]) -> str | None:
    """Расширение из имени (если валидно), иначе по содержимому. None — формат не поддержан."""
    ext = Path(filename or "").suffix.lower()
    if ext in allowed:
        return ext
    return sniff_extension(head)
