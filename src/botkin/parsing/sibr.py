"""Доменный парсер для водородно-метанового дыхательного теста (СИБР).

qwen3-vl в OCR-режиме устойчиво читает таблицу СИБР, но отдаёт её в разных формах:
столбцами со списками значений или построчно. Для построчного формата мы используем
специализированный промпт, возвращающий строки вида:

    0 мин: H2=7 ppm, CH4=13 ppm, H2+2CH4=33 ppm, O2=17 %

Отсюда парсим 4 показателя на каждое время.
"""
from __future__ import annotations

import re

from botkin.domain.models import LabResult

_SIBR_MARKERS = ("сибр", "водородно-метановый", "дыхательный тест", "lactulose", "лактулоза")
# OCR может распознать только заголовки колонок без слова «СИБР»; газовые показатели
# H2/CH4/O2 вместе — достаточно специфичный паттерн для водородно-метанового теста.
_GAS_MARKERS = ("h2", "ch4", "o2")


def _has_sibr_gases(text: str) -> bool:
    low = text.lower()
    return all(g in low for g in _GAS_MARKERS)

# Модель недетерминирована в формате: может вернуть и
#   "0 мин: H2=7 ppm, CH4=13 ppm, H2+2CH4=33 ppm, O2=17 %"
# и "0 мин: H2=7, CH4=13, H2+2CH4=33, O2=17%".
# Единицы делаем необязательными, пробел перед % тоже.
_ROW_RE = re.compile(
    r"^(?P<time>\d+)\s*мин\s*:?\s*"
    r"H2\s*=\s*(?P<h2>\d+(?:[.,]\d+)?)\*?\s*(?:ppm)?\s*[,;]?\s*"
    r"CH4\s*=\s*(?P<ch4>\d+(?:[.,]\d+)?)\*?\s*(?:ppm)?\s*[,;]?\s*"
    r"H2\+2CH4\s*=\s*(?P<h2_2ch4>\d+(?:[.,]\d+)?)\*?\s*(?:ppm)?\s*[,;]?\s*"
    r"O2\s*=\s*(?P<o2>\d+(?:[.,]\d+)?)\*?\s*%?",
    re.IGNORECASE | re.UNICODE,
)

# Альтернативные формы записи названий газов в выходном имени показателя.
_GAS_NAME = {
    "h2": ("водород H2", "ppm"),
    "ch4": ("метан CH4", "ppm"),
    "h2_2ch4": ("H2 + 2CH4", "ppm"),
    "o2": ("кислород O2", "% КВМ"),
}


def is_sibr_text(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _SIBR_MARKERS) or _has_sibr_gases(low)


def parse_sibr_ocr(text: str) -> list[LabResult]:
    """Построчный OCR-ответ СИБР → плоский список LabResult."""
    rows: list[LabResult] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _ROW_RE.match(line)
        if not match:
            continue
        time_min = int(match.group("time"))
        for key, (name, unit) in _GAS_NAME.items():
            raw = match.group(key)
            rows.append(LabResult(
                analyte_name=f"СИБР-тест: {time_min} минут, {name}",
                value_num=_to_float(raw),
                value_raw=raw,
                unit=unit,
            ))
    return rows


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))
