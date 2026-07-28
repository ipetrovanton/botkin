"""Harvester: вытаскивает строки-показатели из JSON произвольной структуры.

qwen3-vl каждый прогон меняет имена ключей (англ/рус) и обёртку. Harvester не
полагается на фиксированные имена: распознаёт роль поля по алиасу ключа (по подстроке,
рус+англ), а при незнакомом ключе — по виду значения.
"""
from __future__ import annotations

import json
from typing import Optional

import json_repair

from botkin.domain.models import LabResult
from botkin.parsing.rows import dedup_rows
from botkin.parsing.scalars import (
    looks_like_number, looks_like_ref, parse_lab_value, parse_reference_range,
)

_KEY_COMMENT = ("comment", "коммент", "примечан")
_KEY_REF = ("reference", "range", "норма", "норматив", "диапазон", "ref", "norm")
_KEY_UNIT = ("unit", "единиц", "ед.изм", "ед_изм", "размерност")
_KEY_VALUE = ("value", "result", "результат", "значен")
_KEY_NAME = ("parameter", "name", "analyte", "показател", "исследован", "параметр",
             "наименован", "тест", "test")


def _key_role(key: str) -> Optional[str]:
    """Роль поля по имени ключа. Порядок важен: ref/unit/comment до value/name."""
    k = " ".join(str(key).strip().lower().replace("ё", "е").split())
    if not k:
        return None
    if any(t in k for t in _KEY_COMMENT):
        return "comment"
    if any(t in k for t in _KEY_REF):
        return "ref"
    if any(t in k for t in _KEY_UNIT):
        return "unit"
    if any(t in k for t in _KEY_VALUE):
        return "value"
    if any(t in k for t in _KEY_NAME):
        return "name"
    return None


def _harvest_row(d: dict) -> Optional[LabResult]:
    """Одна строка-показатель (dict с произвольными именами полей) → LabResult."""
    scalars = [(str(k), str(v).strip()) for k, v in d.items()
               if v is not None and not isinstance(v, (list, dict)) and str(v).strip()]
    if not scalars:
        return None

    name = value_str = unit = ref = comment = None
    taken: set[int] = set()
    # 1) по ролям ключей
    for i, (k, s) in enumerate(scalars):
        role = _key_role(k)
        if role == "name" and name is None:
            name = s
            taken.add(i)
        elif role == "value" and value_str is None:
            value_str = s
            taken.add(i)
        elif role == "unit" and unit is None:
            unit = s
            taken.add(i)
        elif role == "ref" and ref is None:
            ref = s
            taken.add(i)
        elif role == "comment" and comment is None:
            comment = s
            taken.add(i)
    # 2) добор по содержимому из незанятых полей
    if ref is None:
        for i, (k, s) in enumerate(scalars):
            if i not in taken and looks_like_ref(s):
                ref = s
                taken.add(i)
                break
    if value_str is None:
        for i, (k, s) in enumerate(scalars):
            if i not in taken and looks_like_number(s):
                value_str = s
                taken.add(i)
                break
    if name is None:
        cand = [(i, s) for i, (k, s) in enumerate(scalars)
                if i not in taken and not looks_like_number(s)]
        if cand:
            i, name = max(cand, key=lambda x: len(x[1]))
            taken.add(i)
    if not name:
        return None

    value_num, value_text = parse_lab_value(value_str)
    ref_low, ref_high, ref_operator, ref_text = parse_reference_range(ref)
    return LabResult(
        analyte_name=name, value_num=value_num, value_text=value_text,
        value_raw=value_str, unit=unit,
        ref_low=ref_low, ref_high=ref_high, ref_operator=ref_operator, ref_text=ref_text,
        comments=comment,
    )


def _is_row_dict(d) -> bool:
    """dict «похож на строку показателя»: ≥2 скаляра и есть значение/норма (по виду или ключу)."""
    if not isinstance(d, dict):
        return False
    scal = [(k, v) for k, v in d.items() if not isinstance(v, (list, dict))]
    if len(scal) < 2:
        return False
    by_content = any(
        v is not None and (looks_like_number(str(v)) or looks_like_ref(str(v)))
        for k, v in scal
    )
    by_key = any(_key_role(str(k)) in ("value", "ref") for k, v in scal)
    return by_content or by_key


def _collect_tables(node, out: list) -> None:
    """Рекурсивно ищет списки строк-показателей в произвольном JSON."""
    if isinstance(node, list):
        rows = [x for x in node if _is_row_dict(x)]
        dicts = [x for x in node if isinstance(x, dict)]
        if rows and len(rows) == len(dicts):
            out.append(rows)
        else:
            for x in node:
                _collect_tables(x, out)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_tables(v, out)


def harvest_lab_rows(data) -> list[LabResult]:
    """Сырой JSON ответа модели (любой структуры) → список LabResult по содержимому."""
    tables: list = []
    _collect_tables(data, tables)
    out: list[LabResult] = []
    for table in tables:
        for item in table:
            row = _harvest_row(item)
            if row is not None:
                out.append(row)
    return dedup_rows(out)


def loads_json(text: str):
    """Толерантный json.loads сырого ответа модели. None, если не разобрать.

    Сначала пробует стандартный json.loads (быстрее), при неудаче — json_repair
    (чинит обрезанные кавычки, скобки, запятые)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return json_repair.loads(text)


def salvage_json_objects(text: str) -> list[dict]:
    """Извлекает JSON-объекты из (возможно оборванного) текста.

    Использует json_repair для восстановления повреждённого JSON,
    затем рекурсивно собирает все dict-объекты из результата.
    """
    if not text:
        return []
    repaired = json_repair.loads(text)
    return _extract_all_dicts(repaired)


def _extract_all_dicts(node) -> list[dict]:
    """Рекурсивно собирает все dict из произвольной структуры."""
    if isinstance(node, dict):
        return [node] + [d for v in node.values() for d in _extract_all_dicts(v)]
    if isinstance(node, list):
        return [d for item in node for d in _extract_all_dicts(item)]
    return []
