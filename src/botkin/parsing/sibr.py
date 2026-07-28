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
from botkin.parsing.constants import SIBR_MARKERS, GAS_MARKERS, GAS_NAME, SIBR_ROW_RE
from botkin.parsing.tokens import to_float


def _has_sibr_gases(text: str) -> bool:
    low = text.lower()
    return all(g in low for g in GAS_MARKERS)



def is_sibr_text(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in SIBR_MARKERS) or _has_sibr_gases(low)


def parse_sibr_ocr(text: str) -> list[LabResult]:
    """Построчный OCR-ответ СИБР → плоский список LabResult."""
    rows: list[LabResult] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = SIBR_ROW_RE.match(line)
        if not match:
            continue
        time_min = int(match.group("time"))
        # Реальные бланки (Инвитро и др.) называют точку t=0 «базовая проба», а не «0 минут».
        time_label = "базовая проба" if time_min == 0 else f"{time_min} минут"
        for key, (name, unit) in GAS_NAME.items():
            raw = match.group(key)
            rows.append(LabResult(
                analyte_name=f"СИБР-тест: {time_label}, {name}",
                value_num=to_float(raw),
                value_raw=raw,
                unit=unit,
            ))
    return rows
