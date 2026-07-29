"""СИБР-OCR: специализированный VLM-запрос для водородно-метанового теста + voting."""
from __future__ import annotations

import logging
import time

from botkin.config import VLM_MODEL, VLM_MAX_TOKENS, RAW_LOG_LIMIT
from botkin.domain.models import LabResult
from botkin.llm.client import get_raw_client, default_options, model_name
from botkin.llm.image_ocr import messages_from_images
from botkin.parsing.sibr import parse_sibr_ocr

log = logging.getLogger(__name__)

_SIBR_OCR_PROMPT = (
    "На изображении — таблица водородно-метанового дыхательного теста с лактулозой (СИБР). "
    "Время в минутах идёт по строкам, газовые показатели по колонкам. "
    "Верни таблицу строго в формате: одна строка на каждое время, "
    'формат: "<время> мин: H2=<ppm>, CH4=<ppm>, H2+2CH4=<ppm>, O2=<%>". '
    "Ничего не придумывай и не пропускай."
)
# Минимум строк: полная таблица СИБР даёт 8 временных точек × 4 газа = 32 показателя.
_SIBR_MIN_ROWS = 16

# СИБР-таблица тоже читается одним VLM-вызовом при temperature=0.0, но GPU-инференс
# не гарантирует побитовую детерминированность — редкий сбой формата/цифры роняет
# результат ниже _SIBR_MIN_ROWS и весь блок из 32 показателей отбрасывается. Voting
# по аналогии с Андрофлор устраняет эти стохастические провалы.
_SIBR_VOTING_TRIES = 3


def call_sibr_ocr(b64_images: list[str], doc_name: str) -> str:
    """Специализированный OCR-запрос для таблицы СИБР (возвращает построчный формат)."""
    messages = messages_from_images("Ты — точный OCR медицинских таблиц.", _SIBR_OCR_PROMPT, b64_images)
    client = get_raw_client()
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model_name(VLM_MODEL),
        messages=messages,
        max_tokens=VLM_MAX_TOKENS,
        temperature=0.0,
        extra_body={"options": {**default_options(), "temperature": 0.0}},
    )
    elapsed = time.perf_counter() - t0
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
                if len(rows) >= _SIBR_MIN_ROWS:
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("[SIBR_VOTE] Doc: '%s' | попытка %d упала: %s", doc_name, i + 1, e)
    return rows
