"""Нормализация дат из разных форматов к единому datetime (ISO).

Использует гибридный подход: datetime.fromisoformat для ISO-форматов
(dateparser не парсит ISO с временем), dateparser для остальных
(русские месяцы, числовые форматы, "г." suffix).
"""
from __future__ import annotations

from datetime import datetime

import dateparser


def parse_date(value: str | datetime | None) -> tuple[datetime | None, str | None]:
    """Парсит дату из строки множества форматов.

    Возвращает (datetime | None, сырая_строка | None). Сырая строка возвращается
    только для текстового входа (для хранения оригинала из документа).
    """
    if value is None or isinstance(value, datetime):
        return (value, None)
    if not isinstance(value, str):
        return (None, None)

    raw_out = value
    cleaned = value.strip()

    # ISO с временем — fromisoformat обрабатывает нативно (dateparser не может)
    try:
        return (datetime.fromisoformat(cleaned.replace("z", "+00:00")), raw_out)
    except ValueError:
        pass

    # Русские месяцы, числовые форматы, "г." suffix — dateparser handles all
    dt = dateparser.parse(cleaned, languages=["ru"])
    return (dt, raw_out) if dt else (None, raw_out)
