"""Нормализация числовых значений: десятичная запятая → точка."""
from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_decimal(raw: str | int | float | None) -> tuple[float | None, str | None]:
    """Парсит число из значения.

    Возвращает (нормализованное_число | None, сырая_строка | None).
    Сырая строка возвращается только для текстового входа (для хранения оригинала).
    """
    if raw is None:
        return (None, None)
    if isinstance(raw, (int, float)):
        return (float(raw), None)

    raw_out = str(raw)
    # Убираем разделители тысяч (пробел/NBSP) и приводим запятую к точке.
    cleaned = raw_out.replace(" ", "").replace(" ", "").replace(",", ".")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return (None, raw_out)
    try:
        return (float(match.group()), raw_out)
    except ValueError:
        return (None, raw_out)
