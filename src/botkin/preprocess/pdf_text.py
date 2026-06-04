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


def reconstruct_lines(path: Path, y_tol: float | None = None) -> list[str]:
    """Все страницы PDF → список физических строк в порядке документа."""
    tol = TEXT_LAYER_Y_TOLERANCE if y_tol is None else y_tol
    out: list[str] = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            out.extend(_page_lines(page, tol))
    return out
