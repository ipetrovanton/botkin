"""Salvage полных объектов-строк из обрезанного/таймаутного ответа модели.

Когда instructor падает на обрезанном JSON (таймаут, лимит токенов), сырой текст
ответа может содержать полные JSON-объекты строк таблицы — их можно спасти.
"""
from __future__ import annotations

import logging

from botkin.domain.models import LabResult
from botkin.exceptions import ExtractionError
from botkin.parsing.harvester import harvest_lab_rows, loads_json, salvage_json_objects
from botkin.parsing.rows import RawAnalysis, rows_from_raw

log = logging.getLogger(__name__)


def raw_text_from_exc(exc: Exception) -> str:
    """Сырой текст последнего ответа модели из instructor-исключения (для salvage)."""
    raw = getattr(exc, "raw_text", None)
    if isinstance(raw, str) and raw:
        return raw
    comp = getattr(exc, "last_completion", None) or getattr(exc.__cause__, "last_completion", None)
    try:
        content = comp.choices[0].message.content
        return content if isinstance(content, str) else ""
    except (AttributeError, IndexError, TypeError):
        return ""


def salvage_rows(exc: ExtractionError) -> list[LabResult]:
    """Спасти полные объекты-строки из обрезанного/таймаутного ответа модели (общий путь)."""
    objs = salvage_json_objects(raw_text_from_exc(exc))
    return harvest_lab_rows(objs) if objs else []


def rows_or_harvest(raw: RawAnalysis) -> list[LabResult]:
    """Структурный разбор RawAnalysis; пусто (чужие ключи) → harvester по сырому JSON."""
    rows = rows_from_raw(raw)
    if rows:
        return rows
    data = loads_json(_raw_content(raw))
    return harvest_lab_rows(data) if data is not None else []


def _raw_content(response: object) -> str:
    """Сырой текст ответа модели до парсинга (для диагностики «тихого» []). '' если недоступен."""
    try:
        content = response._raw_response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content if isinstance(content, str) else ""
