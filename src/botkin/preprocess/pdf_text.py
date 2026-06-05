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
    """Слова страницы → физические строки (кластеризация по Y, сортировка по X)."""
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return []
    words = sorted(words, key=lambda w: (w[1], w[0]))  # по y0, затем x0
    clusters: list[tuple[float, list]] = []  # (опорный y0, слова)
    for w in words:
        y0 = w[1]
        if clusters and abs(y0 - clusters[-1][0]) <= y_tol:
            clusters[-1][1].append(w)
        else:
            clusters.append((y0, [w]))
    lines = []
    for _y, group in clusters:
        ordered = sorted(group, key=lambda w: w[0])
        lines.append(" ".join(w[4] for w in ordered).strip())
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
