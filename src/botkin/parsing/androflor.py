from __future__ import annotations

import json
import re
from pathlib import Path

from botkin.domain.models import LabResult

# Канонические имена и синонимы загружаются из JSON-файлов.
_REF_DIR = Path(__file__).parent.parent / "reference" / "androflor"
_ANDROFLOR_CANONICAL_NAMES: list[str] = json.loads(
    (_REF_DIR / "names.json").read_text(encoding="utf-8")
)
_ANDROFLOR_SYNONYMS: dict[str, str] = json.loads(
    (_REF_DIR / "synonyms.json").read_text(encoding="utf-8")
)




def _levenshtein(a: str, b: str) -> int:
    """Классический Левенштейн без оптимизаций — строки короткие (≤60 символов)."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = curr
    return prev[-1]


def _fuzzy_match_name(ocr_name: str) -> str | None:
    """Маппинг OCR-искажённого имени на каноническое из словаря.

    Порог = max(3, len(name) // 4) — допускаем больше ошибок на длинных именах.
    При case-insensitive совпадении возвращаем каноническое имя (нормализуем регистр).
    Возвращает каноническое имя или None, если близкого совпадения нет.
    """
    low_ocr = ocr_name.lower().strip()
    # Точный синоним/аббревиатура (справочник ДНК-Технология) — до fuzzy-поиска.
    if low_ocr in _ANDROFLOR_SYNONYMS:
        return _ANDROFLOR_SYNONYMS[low_ocr]
    # Case-insensitive совпадение — нормализуем регистр к каноническому
    for canonical in _ANDROFLOR_CANONICAL_NAMES:
        if low_ocr == canonical.lower():
            return canonical
    best_name: str | None = None
    best_dist = 999
    for canonical in _ANDROFLOR_CANONICAL_NAMES:
        low_can = canonical.lower()
        dist = _levenshtein(low_ocr, low_can)
        threshold = max(3, len(low_can) // 4)
        if dist < best_dist and dist <= threshold:
            best_dist = dist
            best_name = canonical
    return best_name

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

# Reading-order формат (PaddleOCR-VL с task-токеном "OCR:"/"Table Recognition:"): модель
# читает таблицу по столбцам сверху вниз, поэтому имя показателя и его значения оказываются
# на отдельных строках в порядке визуального обхода, а не "имя: значение" на одной строке.
# Разбираем как конечный автомат: имя → количественное "10 X" → опциональное относительное
# "-X (Y%)"; номер строки таблицы и повторное "10 X" (глобальная референсная строка) — шум.
_QUANTITY_ONLY_RE = re.compile(r"^10\s+(?P<value>\d+(?:[.,]\d+)?)\s*$")
# Split-value: PaddleOCR-VL иногда рвёт "10 5.7" на две строки — "10" и "5.7" отдельно.
_BARE_10_RE = re.compile(r"^10\s*$")
_BARE_VALUE_RE = re.compile(r"^(?P<value>\d+(?:[.,]\d+)?)\s*$")
_RELATIVE_ONLY_RE = re.compile(r"^(?P<value>[+-]\d+(?:[.,]\d+)?)\s*\(")
_ROW_INDEX_RE = re.compile(r"^\d+$")
# Фантомные числа: PaddleOCR-VL галлюцинирует колонку возрастающих номеров (10, 20, 45, 65, ...).
# Это НЕ значения Lg (реальные Lg = 3.0–6.0), отфильтровываем по разумному диапазону.
_LG_MAX = 15.0


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


def _fix_merged_value(raw_value: str) -> float:
    """Исправление OCR-ошибки слияния пробела: '10 47' → 4.7, '1047' → 4.7.

    PaddleOCR-VL часто теряет пробел/запятую между '10' и значением Lg:
    '10 4.7' → '10 47' или '1047'. Реальные Lg-значения в Андрофлор = 3.0–6.0,
    поэтому если число > _LG_MAX, делим на 10 (сдвиг запятой).
    """
    val = _to_float(raw_value)
    while val > _LG_MAX:
        val /= 10.0
    return val


def _apply_fuzzy_name(name: str) -> str:
    """Маппинг OCR-искажённого имени на каноническое через fuzzy matching."""
    matched = _fuzzy_match_name(name)
    return matched if matched else name


def _parse_single_line_format(text: str) -> list[LabResult]:
    rows: list[LabResult] = []
    for raw_line in _normalize_multiline(text).splitlines():
        line = raw_line.strip()
        if not line or "не выяв" in line.lower():
            continue
        match = _VALUE_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip().rstrip(":").strip()
        name = _apply_fuzzy_name(name)
        value = _fix_merged_value(match.group("value"))
        if value > _LG_MAX:
            continue
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


def _parse_reading_order_format(text: str) -> list[LabResult]:
    rows: list[LabResult] = []
    pending_name: str | None = None
    has_value = False
    pending_10 = False  # флаг: предыдущая строка была "10" без значения (split-value)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Split-value: "10" на отдельной строке — проверяем ДО _ROW_INDEX_RE,
        # иначе "10" матчится как номер строки и пропускается.
        if _BARE_10_RE.match(line):
            pending_10 = True
            continue
        if _ROW_INDEX_RE.match(line):
            continue
        if "не выяв" in line.lower():
            pending_name, has_value, pending_10 = None, False, False
            continue
        if m := _RELATIVE_ONLY_RE.match(line):
            if pending_name and has_value:
                rows.append(LabResult(
                    analyte_name=f"{pending_name}, относительный показатель",
                    value_num=_to_float(m.group("value")),
                    value_raw=m.group("value"),
                    unit="Lg(X/СВМО)",
                ))
            pending_name, has_value, pending_10 = None, False, False
            continue
        # Split-value: "10" на одной строке, значение на следующей
        if _BARE_10_RE.match(line):
            pending_10 = True
            continue
        if pending_10 and pending_name and not has_value:
            if m := _BARE_VALUE_RE.match(line):
                value = _fix_merged_value(m.group("value"))
                if value <= _LG_MAX:
                    rows.append(LabResult(
                        analyte_name=pending_name,
                        value_num=value,
                        value_raw=m.group("value"),
                        unit="Lg",
                    ))
                    has_value = True
                pending_10 = False
                continue
        pending_10 = False
        if m := _QUANTITY_ONLY_RE.match(line):
            if pending_name and not has_value:
                value = _fix_merged_value(m.group("value"))
                if value <= _LG_MAX:
                    rows.append(LabResult(
                        analyte_name=pending_name,
                        value_num=value,
                        value_raw=m.group("value"),
                        unit="Lg",
                    ))
                    has_value = True
            continue  # повторная "10 X" без ожидающего имени — шумовая референсная строка
        # иначе — кандидат в имя показателя (заголовок группы без значений просто не даст строк)
        name = _apply_fuzzy_name(line)
        pending_name, has_value = name, False
    return rows


def parse_androflor_ocr(text: str) -> list[LabResult]:
    """Разбор в двух форматах (однострочный и reading-order) — берём тот, что дал больше строк.

    Формат зависит от модели: qwen3-vl отдаёт "имя: 10 X -Y" на одной строке, PaddleOCR-VL
    (task-токен "OCR:"/"Table Recognition:") — имя и значения раздельными строками в порядке
    визуального обхода таблицы. Выбор без привязки к имени модели: формат может неявно
    зависеть от промпта, а не только от модели.
    """
    single_line = _parse_single_line_format(text)
    reading_order = _parse_reading_order_format(text)
    return reading_order if len(reading_order) > len(single_line) else single_line


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))
