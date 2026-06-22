"""Сырая схема ответа VLM и сборка её в список LabResult с дедупом."""
from __future__ import annotations

from typing import Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from botkin.domain.models import LabResult
from botkin.parsing.scalars import parse_lab_value, parse_reference_range

# Модель естественно отдаёт вложенную структуру tests[].results[] с полями
# parameter/value/reference_range, а не плоский LabResult. Принимаем её как есть
# (+ алиасы на частые синонимы и top-level results как подстраховку), затем маппим.


class _RawRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    parameter: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("parameter", "name", "analyte_name", "test_name"))
    value: Optional[Union[str, float, int]] = Field(
        default=None, validation_alias=AliasChoices("value", "result", "value_num"))
    unit: Optional[str] = None
    reference_range: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("reference_range", "reference", "norm", "ref"))
    comment: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("comment", "comments"))


class _RawTest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_name: Optional[str] = None
    results: list[_RawRow] = []


class RawAnalysis(BaseModel):
    """Верхний уровень сырого ответа: список тестов и/или плоский список строк."""
    model_config = ConfigDict(extra="ignore")
    tests: list[_RawTest] = []
    results: list[_RawRow] = []


def rows_from_raw(raw: RawAnalysis) -> list[LabResult]:
    """Уплощает tests[].results[] (+ top-level results) в список LabResult."""
    rows: list[_RawRow] = list(raw.results)
    for test in raw.tests:
        rows.extend(test.results)
    out: list[LabResult] = []
    for r in rows:
        if not r.parameter:
            continue
        value_num, value_text = parse_lab_value(r.value)
        ref_low, ref_high, ref_operator, ref_text = parse_reference_range(r.reference_range)
        out.append(LabResult(
            analyte_name=r.parameter,
            value_num=value_num,
            value_text=value_text,
            value_raw=str(r.value) if r.value is not None else None,
            unit=r.unit,
            ref_low=ref_low,
            ref_high=ref_high,
            ref_operator=ref_operator,
            ref_text=ref_text,
            comments=r.comment,
        ))
    return dedup_rows(out)


def _row_key(r: LabResult):
    return (r.analyte_name.strip().lower(), r.value_num, r.value_text)


def dedup_rows(rows: list[LabResult]) -> list[LabResult]:
    """Схлопывает одинаковые (имя, значение) строки, сохраняя порядок первого вхождения.

    qwen3-vl при зацикливании повторяет показатели (MCV/MCH/MCHC/RDW дважды) — это
    раздувает ответ до num_predict и грозит обрывом JSON. Дедуп на выходе разбора.
    """
    seen: set = set()
    out: list[LabResult] = []
    for r in rows:
        key = _row_key(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _name_key(r: LabResult) -> str:
    """Ключ показателя по имени (без значения): lower, ё→е, схлопывание пробелов."""
    return " ".join(r.analyte_name.strip().lower().replace("ё", "е").split())


def merge_dedup(base: list[LabResult], extra: list[LabResult]) -> list[LabResult]:
    """Сливает добор постранично с общим вызовом, дедуп по ИМЕНИ показателя.

    Модель недетерминирована в значениях: один показатель в общем вызове и в доборе
    может иметь разные числа (Гемоглобин 13.7 г/дл vs 143 г/л). Дедуп по (имя,значение)
    оставлял бы оба — дубли с противоречием. Ключ по имени: повтор отбрасываем (доверяем
    первому/общему проходу), добавляем только реально новые показатели (потерянная страница).
    """
    seen = {_name_key(r) for r in base}
    out = list(base)
    for r in extra:
        key = _name_key(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def extraction_quality(items: list[LabResult]) -> dict:
    """Сводка качества извлечения — для сравнения конфигов (полнота полей)."""
    return {
        "total": len(items),
        "with_value_num": sum(1 for i in items if i.value_num is not None),
        "with_value_text": sum(1 for i in items if i.value_text),
        "with_ref": sum(1 for i in items if i.ref_low is not None
                        or i.ref_high is not None or i.ref_text),
        "with_unit": sum(1 for i in items if i.unit),
    }
