"""Нормализация дат из разных форматов к единому datetime (ISO)."""
from __future__ import annotations

from datetime import datetime

_MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# Числовые форматы в порядке приоритета. %y покрывает двузначный год.
_NUMERIC_FORMATS = (
    "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%d.%m.%y", "%d/%m/%y", "%d-%m-%y",
    "%Y-%m-%d",
)


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
    cleaned = value.strip().lower().replace(" г.", "").replace("г.", "").strip()

    # 1. Русский месяц прописью: "23 марта 2026"
    parts = cleaned.split()
    if len(parts) == 3 and parts[1] in _MONTHS_RU:
        try:
            day, month_name, year = parts
            return (datetime(int(year), _MONTHS_RU[month_name], int(day)), raw_out)
        except (ValueError, KeyError):
            pass

    # 2. ISO с временем
    try:
        return (datetime.fromisoformat(cleaned.replace("z", "+00:00")), raw_out)
    except ValueError:
        pass

    # 3. Числовые форматы
    for fmt in _NUMERIC_FORMATS:
        try:
            return (datetime.strptime(cleaned, fmt), raw_out)
        except ValueError:
            continue

    return (None, raw_out)
