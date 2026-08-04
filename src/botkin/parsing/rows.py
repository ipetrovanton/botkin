"""Сырая схема ответа VLM и сборка её в список LabResult с дедупом."""
from __future__ import annotations

import re

from typing import Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from botkin.domain.models import LabResult
from botkin.parsing.scalars import looks_like_ref, parse_lab_value, parse_reference_range

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


# Шапка таблицы, если модель всё-таки её напечатала вопреки промпту.
_COMPACT_HEADER_NAMES = frozenset({"имя", "показатель", "параметр", "name", "parameter"})

# Текстовые результаты бланков: значение без цифр, но это именно результат.
_TEXTUAL_VALUE_PREFIXES = (
    "не обнар", "обнаруж", "отриц", "положит", "отсутств", "следы", "норм",
    "выявлен", "не выявлен", "чист", "прозрач", "мутн",
)
_HAS_DIGIT = re.compile(r"\d")


def _looks_like_value(field: str) -> bool:
    """Поле похоже на результат: есть цифра либо это типовой текстовый результат."""
    f = field.strip().lower()
    if not f:
        return False
    return bool(_HAS_DIGIT.search(f)) or f.startswith(_TEXTUAL_VALUE_PREFIXES)


def _drop_group_prefix(parts: list[str]) -> list[str]:
    """Срезает лишнюю первую колонку с названием исследования.

    Модель вопреки промпту иногда добавляет группу: «ОБЩИЙ АНАЛИЗ МОЧИ|Лейкоциты|1|в п/зр.|< 5».
    Тогда parts[1] — это имя показателя, а не значение, и все поля уезжают на одно вправо
    (наблюдалось на sample_012/sample_013: показатели микроскопии теряли значение).
    Опираемся не на промпт, а на форму данных: ищем первое поле, похожее на результат,
    и берём имя непосредственно перед ним.
    """
    if len(parts) <= 2 or _looks_like_value(parts[1]):
        return parts
    for i in range(2, len(parts)):
        if _looks_like_value(parts[i]):
            return parts[i - 1:]
    return parts


def parse_compact_rows(text: str) -> RawAnalysis:
    """Компактный построчный ответ модели «имя|значение|единица|референс» → RawAnalysis.

    Зачем формат вместо JSON-схемы: на строку таблицы ключи JSON ("parameter", "value",
    "unit", "reference_range") стоят больше токенов, чем сами данные. Замер на реальных
    страницах — вывод короче в 2.4 раза, вызов быстрее в 1.9–2.4 раза при том же наборе
    строк. Тот же приём уже используется для АндроФлор/СИБР: сырой текст + детерминированный
    разбор здесь, а не грамматика в декодере.

    Разбор устойчив к типичному шуму модели: markdown-таблицы (ведущий/замыкающий «|»),
    строки-заголовки, разделители вида «---», пустые поля.
    """
    rows: list[_RawRow] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        # Markdown-таблица: срезаем обрамляющие «|», иначе первое поле окажется пустым.
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        parts = _drop_group_prefix(parts)
        name, value = parts[0], parts[1]
        # Разделительная строка markdown («--- | --- | ---») и шапка таблицы.
        if not name or set(name) <= set("-: ") or name.lower() in _COMPACT_HEADER_NAMES:
            continue
        if not value:
            continue
        unit = parts[2] or None if len(parts) > 2 else None
        ref = parts[3] or None if len(parts) > 3 else None
        unit, ref = _fix_unit_ref_fields(unit, ref)
        rows.append(_RawRow(
            parameter=name,
            value=value,
            unit=unit,
            reference_range=ref,
        ))
    return RawAnalysis(results=rows)


def _fix_unit_ref_fields(
    unit: str | None, ref: str | None,
) -> tuple[str | None, str | None]:
    """Восстанавливает unit/ref, если модель переставила колонки.

    Частый сбой (sample_011): «Лейкоциты|4.14|4 - 8,8|» — референс попал в unit,
    unit пуст. Также «имя|val|4 - 8|10^9/л» (unit и ref поменяны местами).
    """
    if unit:
        unit = unit.strip() or None
    if ref:
        ref = ref.strip() or None
    # Хвостовой мусор compact: «%/» → «%».
    if unit and unit.endswith("/") and not looks_like_ref(unit):
        unit = unit.rstrip("/").strip() or None

    if unit and looks_like_ref(unit):
        if not ref:
            return None, unit
        if not looks_like_ref(ref):
            # unit=диапазон, ref=единица → поменять местами
            return ref, unit
    return unit, ref


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
        unit, ref_s = _fix_unit_ref_fields(r.unit, r.reference_range)
        ref_low, ref_high, ref_operator, ref_text = parse_reference_range(ref_s)
        out.append(LabResult(
            analyte_name=r.parameter,
            value_num=value_num,
            value_text=value_text,
            value_raw=str(r.value) if r.value is not None else None,
            unit=unit,
            ref_low=ref_low,
            ref_high=ref_high,
            ref_operator=ref_operator,
            ref_text=ref_text,
            comments=r.comment,
        ))
    return dedup_rows(out)


def _row_key(r: LabResult) -> tuple[str, float | None, str | None]:
    return (_name_key(r), r.value_num, r.value_text)


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
    """Ключ показателя по имени (без значения): lower, ё→е, схлопывание пробелов.

    Хвостовые «:»/«::» (OCR-дубли sample_011 «Лейкоциты::») срезаются.
    """
    name = r.analyte_name.strip().lower().replace("ё", "е").rstrip(":").strip()
    return " ".join(name.split())


# Имена-шапки бланка / метаданные, не показатели (sample_012 EXTRA).
_NOISE_NAME_EXACT = frozenset({
    "исследование", "результат", "результаты", "единицы", "значения",
    "референс", "норма", "комментарии", "комментарий", "исполнитель",
    "внимание", "страница", "пол", "возраст", "врач", "дата",
    "описание бланка результатов",
})
_NOISE_NAME_PREFIXES = (
    "дата ", "саулина ", "перейти на ", "результаты исследований не являются",
    "внимание!", "* результат", "инз:", "www.",
)


def is_noise_row(r: LabResult) -> bool:
    """True, если строка — шапка/метаданные/мусор, а не лабораторный показатель.

    Консервативно: не отбрасываем строки с числовым значением и коротким именем
    (типичный показатель). Не трогаем качественные «не обнаружено» с нормальным именем.
    """
    name = (r.analyte_name or "").strip()
    if not name:
        return True
    key = _name_key(r)
    if not key or key in {"-", "—", "%", "п/з", "в п/зр."}:
        return True
    if key in _NOISE_NAME_EXACT:
        return True
    lower = name.lower()
    if any(lower.startswith(p) for p in _NOISE_NAME_PREFIXES):
        return True
    # Длинная проза без числа (описания методик Андрофлор и т.п.).
    if r.value_num is None and len(name) > 100:
        return True
    # Пустое значение-заглушка «—»/«-» без числа.
    if r.value_num is None and (r.value_text or "").strip() in {"", "—", "-", "–"}:
        if not r.unit and r.ref_low is None and r.ref_high is None:
            return True
    return False


def filter_noise_rows(rows: list[LabResult]) -> list[LabResult]:
    """Убирает шапки/метаданные/пустые заглушки; нормализует хвостовые «:» в имени."""
    out: list[LabResult] = []
    for r in rows:
        if is_noise_row(r):
            continue
        # «Лейкоциты::» → «Лейкоциты» для UI и дальнейшей нормализации.
        cleaned = r.analyte_name.rstrip(":").strip()
        if cleaned != r.analyte_name:
            r.analyte_name = cleaned
        out.append(r)
    return out


def _unit_dimension(unit: str | None) -> str | None:
    """Нормализует единицу к физической размерности для дедупа.

    Конвертируемые варианты одной размерности (г/дл vs г/л, ммоль/л vs мг/дл)
    приводятся к одному ключу, а качественно разные размерности (% vs п/з,
    10^9/л vs п/з) остаются разными.
    """
    if not unit:
        return None
    u = unit.lower().replace("ё", "е").replace(" ", "")
    # Убираем числовые префиксы (10^9, 10^12) и степени.
    u = re.sub(r"10\^?\d+", "", u)
    u = re.sub(r"\d+", "", u)
    u = u.replace("^", "")
    # Массовые единицы (мкг/нг/пг/мг/кг/г → г).
    for prefix in ("мкг", "нг", "пг", "мг", "кг", "г"):
        u = u.replace(prefix, "г")
    # Объёмные единицы (мкл/мл/дл/кл/фл/пл/нл/л → л).
    for prefix in ("мкл", "мл", "дл", "кл", "фл", "пл", "нл", "л"):
        u = u.replace(prefix, "л")
    # Линейные единицы (мм/см/дм/км/м → м).
    for prefix in ("мм", "см", "дм", "км", "м"):
        u = u.replace(prefix, "м")
    return u


def _merge_key(r: LabResult) -> tuple[str, str | None]:
    """Ключ для merge_dedup: имя + нормализованная размерность.

    Относительные единицы (% и ppm) сохраняем как есть: они точно различают
    показатели (СИБР O2 % КВМ vs ppm). Для абсолютных размерностей конвертируемые
    варианты (г/дл vs г/л) схлопываются в один ключ, чтобы отбросить конфликтные
    дубли от модели. Качественно разные размерности (10^9/л vs п/з) остаются
    разными и сохраняются как отдельные показатели.
    """
    unit = r.unit or None
    if unit and ("%" in unit or "ppm" in unit.lower()):
        return (_name_key(r), unit)
    return (_name_key(r), _unit_dimension(unit))


def merge_dedup(base: list[LabResult], extra: list[LabResult]) -> list[LabResult]:
    """Сливает добор постранично с общим вызовом, дедуп по (имя, единица).

    Модель недетерминирована в значениях: один показатель в общем вызове и в доборе
    может иметь разные числа. Ключ по (имя, единица): повтор с той же единицей
    отбрасываем (доверяем первому проходу), а новая единица сохраняется как отдельный
    показатель.

    Исключение: если добор содержит числовое значение, а уже виденный ряд с той же
    парой (имя, единица) текстовый (value_num is None), заменяем его.
    """
    seen: dict[tuple[str, str | None], int] = {}
    out = list(base)
    for i, r in enumerate(out):
        seen[_merge_key(r)] = i
    for r in extra:
        key = _merge_key(r)
        if key not in seen:
            seen[key] = len(out)
            out.append(r)
        elif r.value_num is not None and out[seen[key]].value_num is None:
            # Заменяем текстовый дубль на числовой.
            out[seen[key]] = r
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
