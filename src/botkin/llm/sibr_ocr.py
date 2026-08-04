"""СИБР-OCR: специализированный VLM-запрос для водородно-метанового теста + voting."""
from __future__ import annotations

import logging
import os
import time

from botkin.config import OCR_MODEL, OCR_MAX_TOKENS, OCR_NUM_CTX, RAW_LOG_LIMIT
from botkin.domain.models import LabResult
from botkin.llm.client import get_raw_client, ocr_options, model_name
from botkin.llm.image_ocr import messages_from_images
from botkin.llm.metrics import metrics_of, log_metrics
from botkin.llm.prompts import SIBR_OCR_PROMPT, SIBR_OCR_SYSTEM
from botkin.parsing.sibr import parse_sibr_ocr

log = logging.getLogger(__name__)

# Минимум строк: полная таблица СИБР даёт 8 временных точек × 4 газа = 32 показателя.
# Ниже — не принимаем блок (слишком много пропусков).
_SIBR_MIN_ROWS = 16
# Early-exit voting: «почти полная» таблица — дальше не крутим GPU.
_SIBR_FULL_ROWS = 28

# СИБР-таблица тоже читается одним VLM-вызовом при temperature=0.0, но GPU-инференс
# не гарантирует побитовую детерминированность — редкий сбой формата/цифры роняет
# результат ниже _SIBR_MIN_ROWS и весь блок из 32 показателей отбрасывается. Voting
# по аналогии с Андрофлор устраняет эти стохастические провалы.
_SIBR_VOTING_TRIES = 3


def call_sibr_ocr(b64_images: list[str], doc_name: str) -> str:
    """Специализированный OCR-запрос для таблицы СИБР (возвращает построчный формат)."""
    messages = messages_from_images(SIBR_OCR_SYSTEM, SIBR_OCR_PROMPT, b64_images)
    client = get_raw_client()
    t0 = time.perf_counter()
    extra_body: dict = {"options": {**ocr_options(), "temperature": 0.0}}
    if os.getenv("VLM_DISABLE_THINKING", "").lower() in ("1", "true", "yes", "on"):
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        extra_body["reasoning_effort"] = "none"
        extra_body["options"]["think"] = False
    response = client.chat.completions.create(
        model=model_name(OCR_MODEL),
        messages=messages,
        max_tokens=OCR_MAX_TOKENS,
        temperature=0.0,
        extra_body=extra_body,
    )
    elapsed = time.perf_counter() - t0
    metrics = metrics_of(response, model_name(OCR_MODEL), elapsed, num_ctx=OCR_NUM_CTX)
    log_metrics(metrics, doc_name=doc_name)
    content = response.choices[0].message.content
    text = content if isinstance(content, str) else ""
    log.info("[SIBR_OCR] Doc: '%s' | Elapsed: %.2fs | символов=%d", doc_name, elapsed, len(text))
    log.debug("[SIBR_OCR_RAW] Doc: '%s' | %s", doc_name, text[:RAW_LOG_LIMIT])
    return text


def sibr_ocr_with_voting(b64_images: list[str], doc_name: str) -> list[LabResult]:
    """СИБР-OCR с voting: повтор вызова при недоборе строк, выбор лучшего по числу строк.

    Один и тот же запрос при temperature=0.0 иногда даёт < _SIBR_MIN_ROWS строк из-за
    недетерминированности GPU-инференса (см. _SIBR_VOTING_TRIES). Повторные вызовы того
    же промпта на тех же картинках обычно расходятся с первым и восстанавливают полную
    таблицу.
    """
    sibr_text = call_sibr_ocr(b64_images, doc_name)
    rows = parse_sibr_ocr(sibr_text)
    # Уже почти полная таблица — voting не нужен.
    if len(rows) >= _SIBR_FULL_ROWS:
        return rows
    if len(rows) < _SIBR_MIN_ROWS:
        for i in range(_SIBR_VOTING_TRIES):
            try:
                vote_text = call_sibr_ocr(b64_images, doc_name)
                vote_rows = parse_sibr_ocr(vote_text)
                log.info(
                    "[SIBR_VOTE] Doc: '%s' | попытка %d/%d | строк=%d",
                    doc_name, i + 1, _SIBR_VOTING_TRIES, len(vote_rows),
                )
                if len(vote_rows) > len(rows):
                    rows = vote_rows
                # Early-exit: полный набор или достаточный минимум после улучшения.
                if len(rows) >= _SIBR_FULL_ROWS or len(rows) >= _SIBR_MIN_ROWS:
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("[SIBR_VOTE] Doc: '%s' | попытка %d упала: %s", doc_name, i + 1, e)
    return rows