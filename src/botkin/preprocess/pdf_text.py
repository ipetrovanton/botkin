"""Извлечение строк из текстового слоя PDF без VLM.

Цифровые PDF (ИНВИТРО и т.п.) несут точный текстовый слой: значения дословно,
с десятичными и правильными единицами. Сборка слов в физические строки —
детерминированная (кластеризация по координате Y с толеранцией: значение часто
сидит на 1px ниже имени показателя, наивное округление разрывает строку).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from botkin.config import TEXT_LAYER_MIN_CHARS_PER_PAGE, TEXT_LAYER_Y_TOLERANCE

log = logging.getLogger(__name__)

# Словарь известных продолжений многострочных имён показателей.
_CONTINUATION_PREFIXES = {
    "объем", "объему", "объема", "объемы",
    "эритроцитов", "тромбоцитов", "лейкоцитов", "крови", "мочи",
    "ширины", "распределения", "содержания", "концентрации", "среднего",
}

# Предлоги, после которых имя физически продолжается на следующей строке.
_PREPOSITIONS = {
    "по", "в", "во", "на", "к", "ко", "из", "для", "при", "об", "обо",
    "с", "со", "от", "ото", "без", "до", "под", "подо", "над", "про",
    "через", "между", "ради", "благодаря",
}


def _has_number(line: str) -> bool:
    """True, если в строке есть числовой токен (возможно, с флагом *)."""
    return bool(re.search(r"\d+(?:[.,]\d+)?", line))


def _is_name_continuation(head: str, current: str) -> bool:
    """True, если current — продолжение многострочного имени показателя head.

    Проверяет независимо от наличия числа: используется, когда значение физически
    оказалось между двумя частями имени (sample_009, RDW-CV).
    """
    if not head or not current:
        return False
    first = current.split()[0] if current.split() else ""
    # Аббревиатура в скобках: (MCH), (RDW-SD), (PCT).
    if first.startswith("(") and any(c.isupper() for c in first):
        return True
    # Продолжение имени с маленькой буквы (середина составного термина).
    if first and first[0].islower():
        return True
    # Известные продолжения (без учёта регистра).
    if first.lower() in _CONTINUATION_PREFIXES:
        return True
    # Предыдущая строка заканчивается предлогом.
    head_last = head.split()[-1].lower().rstrip(":;,.—–-") if head.split() else ""
    if head_last in _PREPOSITIONS:
        return True
    return False


def _is_name_head(line: str) -> bool:
    """True, если строка начинается с имени показателя (буква).

    Аббревиатура в скобках в начале — это продолжение предыдущего имени,
    а не самостоятельная голова (см. sample_008: "Ср. содержание ..." + "(MCH) 30.3").
    """
    stripped = line.strip()
    return bool(stripped) and stripped[0].isalpha()


def _is_pure_value_line(line: str) -> bool:
    """True, если строка — чистое значение без имени: начинается с числа."""
    stripped = line.strip()
    return bool(stripped) and bool(re.match(r"^-?\d", stripped)) and not _is_name_head(stripped)


def _paren_balance(s: str) -> int:
    return s.count("(") - s.count(")")


def _merge_continuation_lines(lines: list[str]) -> list[str]:
    """Склеивает строки, которые в PDF разбиты на две физические строки.

    Реальные примеры из sample_008/sample_009:
        Ср. содержание гемоглобина в эритроците
        (MCH) 30.3 pg 27 - 34
    и
        Ширина распределения эритроцитов по
        объем (RDW-SD) 49.6 fL 35-56
    и sample_009 (значение физически оказалось между частями имени):
        Расчетное распределение ширины
        12.00 % 11,6 - 14,8 SysmexXN-1000i
        эритроцитов, КВ (RDW-CV)
    и sample_009 (скобка не закрыта до продолжения):
        PDW ( ширина распределения
        13.40 фл 9,8 - 16,2
        тромбоцитов )
    """
    merged: list[str] = []
    head_index: int | None = None  # индекс последней строки-головы имени

    def _maybe_pull_value_after_head():
        """Если после головы лежит оторванное значение — подтянуть его в конец."""
        if head_index is None:
            return
        if head_index + 1 < len(merged) and _is_pure_value_line(merged[head_index + 1]):
            # Подтягиваем только если скобки в голове сбалансированы: иначе значение
            # оказалось бы внутри пояснения в скобках (sample_009, PDW).
            if _paren_balance(merged[head_index]) == 0:
                merged[head_index] = f"{merged[head_index]} {merged.pop(head_index + 1)}"

    for line in lines:
        # Аббревиатура в скобках в начале — продолжение текущей головы имени.
        if head_index is not None and line.strip().startswith("("):
            merged[head_index] = f"{merged[head_index]} {line}"
            continue

        # Чистое значение без имени.
        if _is_pure_value_line(line) and head_index is not None:
            # Если головная строка содержит несбалансированную скобку, не тянем
            # значение внутрь — сначала должно прийти продолжение имени со
            # закрывающей скобкой (sample_009, PDW).
            if _paren_balance(merged[head_index]) == 0:
                merged[head_index] = f"{merged[head_index]} {line}"
            else:
                merged.append(line)
            continue

        # Строка без числа — возможно, продолжение имени. Ищем голову, даже если
        # между ней и текущей строкой встало чистое значение.
        if not _has_number(line) and head_index is not None:
            if _is_name_continuation(merged[head_index], line):
                merged[head_index] = f"{merged[head_index]} {line}"
                _maybe_pull_value_after_head()
                continue

        # Строка со значением, но начинающаяся с продолжения имени
        # (например, «объем (RDW-SD) 49.6 fL 35-56»).
        if head_index is not None and _is_name_continuation(merged[head_index], line):
            merged[head_index] = f"{merged[head_index]} {line}"
            _maybe_pull_value_after_head()
            continue

        # Новая строка: если это головной имени, запоминаем её индекс.
        merged.append(line)
        if _is_name_head(line):
            head_index = len(merged) - 1
        else:
            head_index = None

    return merged


def _page_lines(page, y_tol: float) -> list[str]:
    """Слова страницы → физические строки (кластеризация по скользящему центроиду Y, сорт. по X).

    Кластер сравнивает слово со своим средним y0 (центроидом), а не с y0 первого слова:
    плавный дрейф базовой линии (каждое слово на доли пункта ниже соседа) иначе копился бы
    от анкера и рвал строку, хотя соседние слова почти на одной высоте.
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return []
    words = sorted(words, key=lambda w: (w[1], w[0]))  # по y0, затем x0
    clusters: list[dict] = []  # {"sum_y": Σy0, "n": слов, "items": [(x0, word)]}
    for x0, y0, _x1, _y1, word, *_rest in words:
        if clusters and abs(y0 - clusters[-1]["sum_y"] / clusters[-1]["n"]) <= y_tol:
            cur = clusters[-1]
            cur["sum_y"] += y0
            cur["n"] += 1
            cur["items"].append((x0, word))
        else:
            clusters.append({"sum_y": y0, "n": 1, "items": [(x0, word)]})
    lines = []
    for cur in clusters:
        ordered = sorted(cur["items"], key=lambda t: t[0])
        lines.append(" ".join(word for _x0, word in ordered).strip())
    return [ln for ln in lines if ln]


class PdfTextData:
    """Результат единственного открытия PDF: постраничные строки + плоский текст."""

    __slots__ = ("pages", "flat_text")

    def __init__(self, pages: list[list[str]], flat_text: str) -> None:
        self.pages = pages
        self.flat_text = flat_text

    @property
    def is_usable(self) -> bool:
        """True если символов/стр ≥ порога и есть цифры — аналог has_usable_text_layer."""
        n_pages = len(self.pages) or 1
        chars_per_page = len(self.flat_text.strip()) / n_pages
        return chars_per_page >= TEXT_LAYER_MIN_CHARS_PER_PAGE and any(
            ch.isdigit() for ch in self.flat_text
        )


def open_pdf(path: Path, y_tol: float | None = None) -> PdfTextData:
    """Открывает PDF ровно один раз и возвращает постраничные строки + плоский текст.

    Объединяет три прежних вызова (has_usable_text_layer / reconstruct_pages / source_text)
    в один проход: pymupdf.open происходит однократно, каждая страница читается одним
    get_text("words") + get_text("text") за один обход.
    """
    tol = TEXT_LAYER_Y_TOLERANCE if y_tol is None else y_tol
    pages: list[list[str]] = []
    text_parts: list[str] = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            raw_lines = _page_lines(page, tol)
            pages.append(_merge_continuation_lines(raw_lines))
            text_parts.append(page.get_text("text"))
    return PdfTextData(pages=pages, flat_text="\n".join(text_parts))


def reconstruct_pages(path: Path, y_tol: float | None = None) -> list[list[str]]:
    """PDF → список страниц, каждая — список физических строк в порядке документа.

    Постраничная раскладка нужна извлечению: одинокий результат на отдельной странице
    (напр. С-реактивный белок без заголовка) теряется, если все страницы свалить в один
    LLM-вызов вместе с большой таблицей. Постранично модель фокусируется на одной странице.
    """
    return open_pdf(path, y_tol).pages


def has_usable_text_layer(path: Path) -> bool:
    """True, если у PDF годный текстовый слой: символов/стр ≥ порога и есть цифры."""
    try:
        return open_pdf(path).is_usable
    except Exception as e:  # pragma: no cover — битый PDF → не годен, упадём в VLM
        log.warning("[TEXTLAYER] не удалось открыть '%s': %s", path.name, e)
        return False
