from __future__ import annotations

import re

from botkin.domain.models import LabResult

_ANDROFLOR_MARKERS = ("андрофлор", "lactobacillus spp", "геномная днк человека")
# Якорь на логарифмический маркер "10 <число>": qwen3-vl недетерминирован и выдаёт строку то
# с двоеточием ("Lactobacillus spp.: 10 4.7 -0.1"), то без ("Lactobacillus spp. 10 4.7 -0.1").
# Поэтому двоеточие необязательно (:?), а имя — нежадное до первого "10 <цифра>". Так разбор
# не зависит от наличия двоеточия (исходный баг: regex требовал ":" и давал 0 строк на
# бесколоночном формате) и не путает внутреннее двоеточие имени ("Сумма: УПМ анаэробы").
_VALUE_RE = re.compile(
    r"^(?P<name>.+?)\s*:?\s*10\s+(?P<value>\d+(?:[.,]\d+)?)"
    r"(?:\s+(?P<relative>[+-]\d+(?:[.,]\d+)?))?"
)

# Многострочный OCR-формат: модель эхом повторяет колонки таблицы построчно
# ("Название показателя: X" / "Количественный результат: 10 5.7" / "Относительный ...: -0.1").
# qwen3-vl недетерминирован: один прогон даёт однострочный формат, другой — многострочный.
# Парсер должен понимать оба, иначе на «неудачном» прогоне строки теряются и маршрут уходит
# в общий _structure_text, который Lg-нотацию "10 5.7" портит в 10.0.
_ML_NAME = re.compile(r"назв\w*\s+показ\w*\s*:\s*(?P<v>.+)", re.IGNORECASE)
_ML_QUANT = re.compile(r"кол\w*\s+результат\w*\s*:\s*(?P<v>.+)", re.IGNORECASE)
_ML_REL = re.compile(r"относит\w*[^:]*:\s*(?P<v>.+)", re.IGNORECASE)
_REL_NUM = re.compile(r"^[+-]\d")


def is_androflor_text(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _ANDROFLOR_MARKERS)


def _normalize_multiline(text: str) -> str:
    """Многострочный OCR-формат → однострочный 'name: 10 5.7 -0.1', понятный основному парсеру.

    Если маркеров многострочного формата ("...показател...") нет — текст уже однострочный,
    возвращаем как есть.
    """
    if "показател" not in text.lower():
        return text
    out: list[str] = []
    name = quant = rel = None

    def flush() -> None:
        nonlocal name, quant, rel
        if name and quant:
            line = f"{name}: {quant}"
            if rel and _REL_NUM.match(rel):
                line += f" {rel}"
            out.append(line)
        name = quant = rel = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := _ML_NAME.match(line):
            flush()  # начался новый блок-показатель → закрываем предыдущий
            name = m.group("v").strip()
        elif m := _ML_QUANT.match(line):
            quant = m.group("v").strip()
        elif m := _ML_REL.match(line):
            rel = m.group("v").strip()
    flush()
    return "\n".join(out) if out else text


def parse_androflor_ocr(text: str) -> list[LabResult]:
    rows: list[LabResult] = []
    for raw_line in _normalize_multiline(text).splitlines():
        line = raw_line.strip()
        if not line or "не выяв" in line.lower():
            continue
        match = _VALUE_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip().rstrip(":").strip()
        value = _to_float(match.group("value"))
        rows.append(LabResult(analyte_name=name, value_num=value, value_raw=match.group("value"), unit="Lg"))
        relative = match.group("relative")
        if relative is not None:
            rows.append(LabResult(
                analyte_name=f"{name}, относительный показатель",
                value_num=_to_float(relative),
                value_raw=relative,
                unit="Lg(X/СВМО)",
            ))
    return rows


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))
