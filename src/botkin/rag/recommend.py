"""Рекомендации по вопросу пациента: RAG-контекст + локальная текстовая LLM.

Контекст собирается из трёх источников:
1. Профиль пациента из БД — свежие отклонения анализов, назначенные лекарства.
2. RAG-ретрив по вопросу — записи справочников ГРЛС/ФСЛИ и health-сводки.
3. Данные носимых устройств за последние 2 недели (агрегаты).

Модель НЕ назначает лечение: промпт жёстко требует опираться на переданную
фактуру и отправлять к врачу за назначениями — это ассистент, а не доктор.
"""
from __future__ import annotations

import json
import logging
import time

from botkin.config import (
    OLLAMA_KEEP_ALIVE, RAG_LIFESTYLE_MODEL, RAG_LIFESTYLE_NUM_PREDICT,
    RAG_RECOMMEND_MODEL, RAG_RECOMMEND_NUM_CTX, RAG_RECOMMEND_NUM_PREDICT,
    RAG_TOP_K, RAG_WEB_ENABLED, RAG_WEB_RESULTS,
)
from botkin.db.connection import get_conn
from botkin.llm.client import get_raw_client
from botkin.llm.prompts import LIFESTYLE_RECOMMEND_SYSTEM, RAG_RECOMMEND_SYSTEM
from botkin.rag import retriever, websearch
from botkin.rag.context import build_patient_context

log = logging.getLogger(__name__)

_RECENT_MEDS_SQL = """
    SELECT medications_json, medications_normalized_json, visit_date
    FROM doctor_reports
    WHERE user_id = ? AND medications_json IS NOT NULL
    ORDER BY visit_date DESC LIMIT 3
"""


def recommend(
    user_id: int, question: str, *, top_k: int = RAG_TOP_K, model: str | None = None,
    use_web: bool | None = None, num_predict: int | None = None,
) -> dict:
    """Ответ на вопрос пациента с RAG-контекстом. Возвращает text + использованные чанки.

    model=None → продакшн-модель RAG_RECOMMEND_MODEL; иначе переопределение (бенчмарк).
    use_web=None → флаг RAG_WEB_ENABLED; True/False форсирует живой веб+PubMed в контекст.
    num_predict=None → RAG_RECOMMEND_NUM_PREDICT. Для thinking-моделей (Qwen3.6, DeepSeek-R1)
    рассуждения идут в reasoning_content и «съедают» бюджет — при малом num_predict финальный
    content не успевает сгенерироваться (пустой ответ). Поэтому в бенче поднимаем лимит."""
    num_predict = num_predict or RAG_RECOMMEND_NUM_PREDICT
    chunks = retriever.search(question, user_id=user_id, top_k=top_k)
    med_names = _extract_med_mentions(user_id)
    for name in med_names[:5]:
        extra = retriever.search(name, sources=["drugs"], user_id=user_id, top_k=2)
        seen = {c["ref_key"] for c in chunks}
        chunks.extend(c for c in extra if c["ref_key"] not in seen)

    context_blocks = [f"[{c['source']}] {c['text']}" for c in chunks]
    user_msg = (
        f"КОНТЕКСТ ПАЦИЕНТА:\n{build_patient_context(user_id)}\n\n"
        f"ВЫДЕРЖКИ ИЗ СПРАВОЧНИКОВ И ДАННЫХ:\n" + "\n\n".join(context_blocks)
    )

    want_web = RAG_WEB_ENABLED if use_web is None else use_web
    web_used = False
    if want_web:
        web_ctx = websearch.gather_context(question, max_web=RAG_WEB_RESULTS)
        if web_ctx:
            web_used = True
            user_msg += ("\n\nСВЕЖИЕ ИСТОЧНИКИ ИЗ ИНТЕРНЕТА (веб-поиск и PubMed, "
                         "проверяй критически, указывай ссылку при использовании):\n" + web_ctx)
    user_msg += f"\n\nВОПРОС ПАЦИЕНТА: {question}"
    messages = [
        {"role": "system", "content": RAG_RECOMMEND_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    the_model = model or RAG_RECOMMEND_MODEL
    # Плотные модели с оффлоадом в RAM (напр. 27B) отвечают >10 мин; таймаут с запасом,
    # ретраи SDK выключаем — иначе медленный, но живой вызов трижды бьётся о таймаут.
    client = get_raw_client(timeout=1800.0).with_options(max_retries=0)
    t0 = time.perf_counter()
    response = _chat(client, the_model, messages, num_predict)
    text, reasoning = _split_message(response)
    # thinking-модели кладут рассуждения в отдельное поле и могут исчерпать num_predict до
    # финального content (пустой ответ). Фолбэк: повтор с think=False — весь бюджет на ответ.
    if not text:
        log.info("Пустой content (рассуждения съели бюджет) — повтор с think=False")
        response = _chat(client, the_model, messages, num_predict, think=False)
        text, reasoning = _split_message(response)
    elapsed = time.perf_counter() - t0
    log.info("Рекомендация за %.1fs, чанков в контексте: %d", elapsed, len(chunks))
    usage = getattr(response, "usage", None)
    return {
        "answer": text,
        "reasoning": reasoning,
        "model": the_model,
        "web_used": web_used,
        "elapsed_s": round(elapsed, 2),
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        } if usage else None,
        "chunks": [
            {"source": c["source"], "ref_key": c["ref_key"], "distance": c["distance"]}
            for c in chunks
        ],
    }


_LIFESTYLE_QUERIES = (
    "рекомендации по образу жизни и физическим нагрузкам",
    "взаимодействие лекарственных препаратов",
)


def recommend_lifestyle(
    user_id: int, *, model: str | None = None, num_predict: int | None = None,
) -> dict:
    """Комплексная рекомендация по образу жизни без вопроса пациента.

    Агрегирует все источники (анализы, заключения врачей, назначения, носимые
    устройства, профиль, внешние факторы) и отдаёт их мощной uncensored-модели
    (RAG_LIFESTYLE_MODEL) с промптом lifestyle_recommend: образ жизни, физнагрузки,
    приём препаратов, межлекарственные взаимодействия.
    """
    num_predict = num_predict or RAG_LIFESTYLE_NUM_PREDICT
    chunks: list[dict] = []
    seen: set[str] = set()
    # RAG-добор вспомогательный: без embed-модели/индекса рекомендация всё равно
    # строится по картине пациента, а не падает целиком.
    try:
        for query in _LIFESTYLE_QUERIES:
            for c in retriever.search(query, user_id=user_id, top_k=RAG_TOP_K // 2 or 1):
                if c["ref_key"] not in seen:
                    seen.add(c["ref_key"])
                    chunks.append(c)
        for name in _extract_med_mentions(user_id)[:8]:
            for c in retriever.search(name, sources=["drugs"], user_id=user_id, top_k=2):
                if c["ref_key"] not in seen:
                    seen.add(c["ref_key"])
                    chunks.append(c)
    except Exception as e:
        log.warning("RAG-добор недоступен (%s) — lifestyle без справочных чанков", e)
        chunks = []

    context_blocks = [f"[{c['source']}] {c['text']}" for c in chunks]
    user_msg = f"КАРТИНА ПАЦИЕНТА:\n{build_patient_context(user_id)}"
    if context_blocks:
        user_msg += "\n\nВЫДЕРЖКИ ИЗ СПРАВОЧНИКОВ:\n" + "\n\n".join(context_blocks)
    user_msg += (
        "\n\nЗАДАЧА: составь комплексную рекомендацию по разделам "
        "«Образ жизни», «Физические нагрузки», «Приём препаратов», «Взаимодействия»."
    )
    messages = [
        {"role": "system", "content": LIFESTYLE_RECOMMEND_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    the_model = model or RAG_LIFESTYLE_MODEL
    client = get_raw_client(timeout=1800.0).with_options(max_retries=0)
    t0 = time.perf_counter()
    response = _chat(client, the_model, messages, num_predict)
    text, reasoning = _split_message(response)
    if not text:
        log.info("Пустой content lifestyle-рекомендации — повтор с think=False")
        response = _chat(client, the_model, messages, num_predict, think=False)
        text, reasoning = _split_message(response)
    elapsed = time.perf_counter() - t0
    log.info("Lifestyle-рекомендация за %.1fs, чанков: %d", elapsed, len(chunks))
    return {
        "answer": text,
        "reasoning": reasoning,
        "model": the_model,
        "elapsed_s": round(elapsed, 2),
        "chunks": [
            {"source": c["source"], "ref_key": c["ref_key"], "distance": c["distance"]}
            for c in chunks
        ],
    }


def _chat(client: object, model: str, messages: list[dict], num_predict: int, think: bool | None = None) -> object:
    """Вызов Ollama /v1. think=False (нативный параметр Ollama) отключает рассуждения —
    весь num_predict уходит в ответ; для нерассуждающих моделей это быстрее и без пустых content."""
    body: dict = {"options": {
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "num_ctx": RAG_RECOMMEND_NUM_CTX,
        "num_predict": num_predict,
        "temperature": 0.3,
    }}
    if think is not None:
        body["think"] = think
    return client.chat.completions.create(
        model=model, messages=messages, max_tokens=num_predict, extra_body=body,
    )


def _split_message(response: object) -> tuple[str, str]:
    """(content, reasoning) из ответа. Reasoning у thinking-моделей — в отдельном поле."""
    msg = response.choices[0].message
    text = (msg.content or "").strip()
    reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
    return text, reasoning


def _extract_med_mentions(user_id: int) -> list[str]:
    """Названия лекарств из последних заключений — для точечного добора из ГРЛС."""
    with get_conn() as conn:
        rows = conn.execute(_RECENT_MEDS_SQL, (user_id,)).fetchall()
    names: list[str] = []
    for r in rows:
        source = r["medications_normalized_json"] or r["medications_json"]
        try:
            items = json.loads(source) or []
        except (json.JSONDecodeError, TypeError):
            continue
        for item in items:
            if isinstance(item, dict):
                name = item.get("canonical") or item.get("raw") or ""
            else:
                name = str(item)
            head = name.split(",")[0].split("(")[0].strip()
            if head and head not in names:
                names.append(head)
    return names
