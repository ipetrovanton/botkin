"""Извлечение строк из текстового слоя PDF без VLM.

Цифровые PDF (ИНВИТРО и т.п.) несут точный текстовый слой: значения дословно,
с десятичными и правильными единицами. Сборка слов в физические строки —
детерминированная (кластеризация по координате Y с толеранцией: значение часто
сидит на 1px ниже имени показателя, наивное округление разрывает строку).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from botkin.config import TEXT_LAYER_MIN_CHARS_PER_PAGE, TEXT_LAYER_Y_TOLERANCE

log = logging.getLogger(__name__)


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


def reconstruct_pages(path: Path, y_tol: float | None = None) -> list[list[str]]:
    """PDF → список страниц, каждая — список физических строк в порядке документа.

    Постраничная раскладка нужна извлечению: одинокий результат на отдельной странице
    (напр. С-реактивный белок без заголовка) теряется, если все страницы свалить в один
    LLM-вызов вместе с большой таблицей. Постранично модель фокусируется на одной странице.
    """
    tol = TEXT_LAYER_Y_TOLERANCE if y_tol is None else y_tol
    pages: list[list[str]] = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            pages.append(_page_lines(page, tol))
    return pages


def reconstruct_lines(path: Path, y_tol: float | None = None) -> list[str]:
    """Все страницы PDF → плоский список физических строк в порядке документа."""
    return [ln for page in reconstruct_pages(path, y_tol) for ln in page]


def source_text(path: Path) -> str:
    """Плоский текст слоя всех страниц (для verbatim-стража)."""
    parts: list[str] = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    return "\n".join(parts)


def has_usable_text_layer(path: Path) -> bool:
    """True, если у PDF годный текстовый слой: символов/стр ≥ порога и есть цифры."""
    try:
        with pymupdf.open(str(path)) as doc:
            n_pages = doc.page_count or 1
            text = "".join(page.get_text("text") for page in doc)
    except Exception as e:  # pragma: no cover — битый PDF → не годен, упадём в VLM
        log.warning("[TEXTLAYER] не удалось открыть '%s': %s", path.name, e)
        return False
    chars_per_page = len(text.strip()) / n_pages
    has_digit = any(ch.isdigit() for ch in text)
    return chars_per_page >= TEXT_LAYER_MIN_CHARS_PER_PAGE and has_digit
