"""Извлечение даты исследования/приёма из текста (МРТ, ЭКГ, приём).

Не путать с датой рождения: приоритет у явных меток «Дата исследования»,
«Дата приёма», «Дата осмотра».
"""
from __future__ import annotations

import re
from datetime import datetime

from botkin.normalize.dates import parse_date

# Явные метки даты исследования / приёма (не рождения).
_LABELED_DATE_RE = re.compile(
    r"(?:"
    r"дата\s+(?:исследования|исслед\.?|при[её]ма|осмотра|визита|записи|проведения)|"
    r"(?:исследован[иея]|при[её]м|осмотр)\s*(?:от)?"
    r")"
    r"\s*[:\s]\s*"
    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})",
    re.IGNORECASE,
)

# Любая календарная дата DD.MM.YYYY / DD/MM/YYYY.
_ANY_DATE_RE = re.compile(r"\b(\d{1,2}[./]\d{1,2}[./]\d{4})\b")

# Контекст даты рождения — не брать как visit_date.
_BIRTH_NEAR_RE = re.compile(
    r"(?:рожд|birth|д\.?\s*р\.?|дата\s+рождения)",
    re.IGNORECASE,
)


def extract_visit_date_from_text(text: str) -> datetime | None:
    """Достаёт дату исследования/приёма из OCR/протокола.

    1) Метка «Дата исследования: 17.11.2024» и аналоги.
    2) Иначе первая дата, не стоящая рядом с «рождения».
    """
    if not (text or "").strip():
        return None

    m = _LABELED_DATE_RE.search(text)
    if m:
        dt, _ = parse_date(m.group(1))
        if dt is not None:
            return dt

    for m in _ANY_DATE_RE.finditer(text):
        start = max(0, m.start() - 40)
        ctx = text[start:m.end() + 10]
        if _BIRTH_NEAR_RE.search(ctx):
            continue
        dt, _ = parse_date(m.group(1))
        if dt is not None:
            return dt
    return None


def report_text_blob(report: object) -> str:
    """Склеивает текстовые поля DoctorReport для поиска даты."""
    parts: list[str] = []
    for attr in ("diagnosis", "anamnesis", "department", "doctor_name"):
        val = getattr(report, attr, None)
        if val:
            parts.append(str(val))
    for attr in ("recommendations", "complaints", "medications"):
        for item in getattr(report, attr, None) or []:
            if item:
                parts.append(str(item))
    return "\n".join(parts)
