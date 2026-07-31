"""Инфраструктура VLM-OCR: подготовка сообщений, вызов raw-клиента для OCR-режима."""
from __future__ import annotations

import logging
import os
import time

from botkin.config import OCR_MODEL, OCR_MAX_TOKENS, OCR_NUM_CTX, RAW_LOG_LIMIT
from botkin.llm.client import get_raw_client, ocr_options, model_name
from botkin.llm.metrics import metrics_of, log_metrics
from botkin.llm.prompts import IMAGE_OCR_PROMPT, IMAGE_OCR_SYSTEM

log = logging.getLogger(__name__)

# Task-токен PaddleOCR-VL для повторного OCR-запроса на плотных Lg-таблицах (АндроФлор).
# Модель специально обучена отвечать на короткие task-токены ("OCR:"/"Table Recognition:"),
# а не на диалоговые инструкции — конверсационный IMAGE_OCR_PROMPT (llm/prompts/image_ocr.md) на плотной
# Lg-нотации уводит её off-distribution (галлюцинация псевдо-арифметической прогрессии
# "10 5.7, 10 4.8, 10 3.6, ..." вместо реальных значений; проверено вручную на sample_006).
# См. HF card PaddlePaddle/PaddleOCR-VL-1.6, раздел PROMPTS (2026-05-28).
_PADDLEOCR_TABLE_TASK_TOKEN = "Table Recognition:"

# Число попыток на транзиентную 500-ошибку llama-server ("peg-native format" — известная
# нестабильность GGUF-порта PaddleOCR-VL, см. github.com/ggml-org/llama.cpp/pull/18825).
# SDK-ретраи openai-клиента по умолчанию (max_retries=2) не всегда достаточны на практике.
_IMAGE_OCR_TRANSIENT_RETRIES = 5


def messages_from_images(system_prompt: str, instruction: str, b64_images: list[str]) -> list[dict]:
    """system_prompt='' пропускает системное сообщение — нужно для task-токенов PaddleOCR-VL,
    которая обучена на голый user-текст ("OCR:"/"Table Recognition:") без диалоговой обвязки.
    """
    content: list[dict] = [{"type": "text", "text": instruction}]
    for b64 in b64_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})
    return messages


def call_image_ocr(b64_images: list[str], doc_name: str, task_token: str | None = None) -> str:
    if task_token is not None:
        messages = messages_from_images("", task_token, b64_images)
    else:
        messages = messages_from_images(IMAGE_OCR_SYSTEM, IMAGE_OCR_PROMPT, b64_images)
    client = get_raw_client()
    t0 = time.perf_counter()
    last_exc: Exception | None = None
    for attempt in range(_IMAGE_OCR_TRANSIENT_RETRIES):
        try:
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
            break
        except Exception as e:  # noqa: BLE001 — транзиентная 500 от llama-server, не наша ошибка схемы
            last_exc = e
            log.warning("[IMAGE_OCR_RETRY] Doc: '%s' | попытка %d/%d упала: %s",
                        doc_name, attempt + 1, _IMAGE_OCR_TRANSIENT_RETRIES, e)
    else:
        raise last_exc  # все попытки исчерпаны
    elapsed = time.perf_counter() - t0
    content = response.choices[0].message.content
    text = content if isinstance(content, str) else ""
    metrics = metrics_of(response, model_name(OCR_MODEL), elapsed, num_ctx=OCR_NUM_CTX)
    log_metrics(metrics, doc_name=doc_name)
    log.info("[IMAGE_OCR] Doc: '%s' | Elapsed: %.2fs | символов=%d | task_token=%s",
              doc_name, elapsed, len(text), task_token or "-")
    log.debug("[IMAGE_OCR_RAW] Doc: '%s' | %s", doc_name, text[:RAW_LOG_LIMIT])
    return text
