"""Андрофлор-OCR: voting с task-токенами PaddleOCR-VL на плотных Lg-таблицах."""
from __future__ import annotations

import logging
from typing import Callable

from botkin.domain.models import LabResult
from botkin.llm.image_ocr import call_image_ocr, _PADDLEOCR_TABLE_TASK_TOKEN
from botkin.parsing.androflor import parse_androflor_ocr

log = logging.getLogger(__name__)

# Порог строк, ниже которого «андрофлор-страница» считается описанием бланка, а не таблицей.
_ANDROFLOR_MIN_ROWS = 4

# Разрешение для task-токен retry на плотных таблицах. PaddleOCR-VL официально работает как
# ВТОРОЙ этап двухэтапного пайплайна: отдельная модель PP-DocLayout-V3 сначала режет страницу
# на простые под-изображения (одна ячейка/абзац), и только потом 0.9B VLM читает каждый crop.
# Мы подаём модели целую сложную страницу разом (у нас нет layout-детектора) — на дефолтном
# IMAGE_EXTRACT_LONG_SIDE=2200 с upscale/enhance (тюнинг под qwen3-vl) модель хаотично
# галлюцинирует. Эмпирически подобранное меньшее разрешение без enhance/upscale снижает частоту
# срыва (проверено вручную на sample_006), хотя полностью не устраняет нестабильность модели —
# см. github.com/ggml-org/llama.cpp/pull/18825 (генеративная часть всего 0.3B, "error-prone").
_ANDROFLOR_RETRY_LONG_SIDE = 1264

# Число OCR-попыток с разными task-токенами для voting на Андрофлор.
# PaddleOCR-VL стохастичен: один прогон даёт 2 строки, другой — 11. Делаем N вызовов
# с чередованием task-токенов ("Table Recognition:" / "OCR:") и разрешений (основное / low-res),
# выбираем результат с максимальным числом распарсенных строк.
_ANDROFLOR_VOTING_TRIES = 3

# Early-exit: полная таблица Андрофлор ~20 строк. Если voting уже набрал столько —
# дальнейшие попытки не улучшают recall, только жгут GPU (qwen3-vl: ~15–20 с/вызов).
_ANDROFLOR_FULL_ROWS = 18


def androflor_voting(
    b64_images: list[str],
    doc_name: str,
    initial_rows: list[LabResult],
    low_res_retry_fn: "Callable[[], list[str]] | None" = None,
) -> list[LabResult]:
    """Мульти-вызов + voting для Андрофлор-таблицы.

    PaddleOCR-VL стохастичен — один прогон даёт 2 строки, другой 11. Делаем N дополнительных
    вызовов с чередованием task-токенов и разрешений, выбираем результат с максимальным числом
    распарсенных строк. Early-exit при наборе «полной» таблицы (≥ _ANDROFLOR_FULL_ROWS).
    Возвращает лучший набор строк (≥ initial_rows по длине).
    """
    if len(initial_rows) >= _ANDROFLOR_MIN_ROWS:
        return initial_rows

    low_res_images = low_res_retry_fn() if low_res_retry_fn else None
    task_tokens = [_PADDLEOCR_TABLE_TASK_TOKEN, "OCR:"]
    image_sets = [b64_images] + ([low_res_images] if low_res_images else [])
    best_rows = initial_rows
    for i in range(_ANDROFLOR_VOTING_TRIES):
        token = task_tokens[i % len(task_tokens)]
        images = image_sets[i % len(image_sets)]
        try:
            vote_text = call_image_ocr(images, doc_name, task_token=token)
            vote_rows = parse_androflor_ocr(vote_text)
            log.info(
                "[ANDROFLOR_VOTE] Doc: '%s' | попытка %d/%d | token=%s | строк=%d",
                doc_name, i + 1, _ANDROFLOR_VOTING_TRIES, token, len(vote_rows),
            )
            if len(vote_rows) > len(best_rows):
                best_rows = vote_rows
            # Полная таблица — дальше не голосуем (скорость без потери recall).
            if len(best_rows) >= _ANDROFLOR_FULL_ROWS:
                log.info(
                    "[ANDROFLOR_VOTE_EARLY_EXIT] Doc: '%s' | строк=%d ≥ %d — стоп",
                    doc_name, len(best_rows), _ANDROFLOR_FULL_ROWS,
                )
                break
        except Exception as e:  # noqa: BLE001
            log.warning("[ANDROFLOR_VOTE] Doc: '%s' | попытка %d упала: %s", doc_name, i + 1, e)
    if len(best_rows) > len(initial_rows):
        log.info(
            "[ANDROFLOR_VOTING_RESULT] Doc: '%s' | строк было=%d, после voting=%d",
            doc_name, len(initial_rows), len(best_rows),
        )
    return best_rows
