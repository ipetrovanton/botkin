"""Рекомендации по вопросу пациента: RAG-контекст + локальная текстовая LLM.

Контекст собирается из трёх источников:
1. Профиль пациента из БД — свежие отклонения анализов, назначенные лекарства.
2. RAG-ретрив по вопросу — записи справочников ГРЛС/ФСЛИ и health-сводки.
3. Данные носимых устройств за последние 2 недели (агрегаты).

Модель НЕ назначает лечение: промпт жёстко требует опираться на переданную
фактуру и отправлять к врачу за назначениями — это ассистент, а не доктор.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

from botkin.config import (
    OLLAMA_KEEP_ALIVE, RAG_RECOMMEND_MODEL, RAG_RECOMMEND_NUM_CTX,
    RAG_RECOMMEND_NUM_PREDICT, RAG_TOP_K, RAG_WEB_ENABLED, RAG_WEB_RESULTS,
)
from botkin.db.connection import get_conn
from botkin.clinical.facts import build_lab_facts, render_lab_facts
from botkin.db.repos import HealthRepo
from botkin.llm.client import get_raw_client
from botkin.rag import retriever, websearch

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — медицинский ассистент сервиса Botkin. Отвечаешь по-русски.

Правила:
1. Опирайся ТОЛЬКО на переданный контекст (анализы пациента, данные носимых устройств,
   выдержки из официальных справочников ГРЛС и ФСЛИ). Не выдумывай значения и препараты.
2. Ты НЕ врач и НЕ назначаешь лечение. Можно: объяснять показатели, обращать внимание
   на отклонения и тренды, напоминать статус препарата в реестре ГРЛС (например,
   «исключён из реестра»), советовать обсудить с врачом.
3. Если препарат в контексте помечен как исключённый или приостановленный — обязательно
   предупреди об этом.
4. Если данных недостаточно — так и скажи, не фантазируй.
5. Если в контексте есть блок «СВЕЖИЕ ИСТОЧНИКИ ИЗ ИНТЕРНЕТА» — можешь опираться на него
   как на дополнительную справку, но проверяй критически и указывай ссылку при цитировании.
6. Завершай ответ напоминанием, что окончательные решения принимает лечащий врач."""

_RECENT_LABS_SQL = """
    SELECT COALESCE(analyte_canonical, analyte_name) AS name, value_num, unit,
           ref_low, ref_high, ref_text, taken_at
    FROM lab_results
    WHERE user_id = ? AND value_num IS NOT NULL
      AND (ref_low IS NOT NULL OR ref_high IS NOT NULL)
      AND (value_num < COALESCE(ref_low, -1e18) OR value_num > COALESCE(ref_high, 1e18))
    ORDER BY taken_at DESC LIMIT 15
"""

_RECENT_MEDS_SQL = """
    SELECT medications_json, medications_normalized_json, visit_date
    FROM doctor_reports
    WHERE user_id = ? AND medications_json IS NOT NULL
    ORDER BY visit_date DESC LIMIT 3
"""


def _patient_context(user_id: int) -> str:
    """Отклонения анализов + назначенные лекарства + агрегаты носимых устройств."""
    parts: list[str] = []
    with get_conn() as conn:
        labs = conn.execute(_RECENT_LABS_SQL, (user_id,)).fetchall()
        if labs:
            facts = build_lab_facts(labs)
            parts.append(render_lab_facts(facts))

        meds_rows = conn.execute(_RECENT_MEDS_SQL, (user_id,)).fetchall()
        med_names: list[str] = []
        for r in meds_rows:
            try:
                med_names.extend(json.loads(r["medications_json"]) or [])
            except (json.JSONDecodeError, TypeError):
                continue
        if med_names:
            parts.append("Назначенные врачами лекарства: " + "; ".join(med_names[:15]))

        health = HealthRepo(conn, user_id)
        since = str(dt.date.today() - dt.timedelta(days=14))
        daily = health.daily_summary(since, str(dt.date.today()) + " 23:59:59")
        if daily:
            by_metric: dict[str, list[dict]] = {}
            for row in daily:
                by_metric.setdefault(row["metric"], []).append(row)
            lines = ["Носимые устройства за 14 дней (дневные агрегаты):"]
            for metric, rows in sorted(by_metric.items()):
                avg = sum(r["avg"] for r in rows) / len(rows)
                unit = rows[0].get("unit") or ""
                lines.append(f"- {metric}: среднее {avg:.1f} {unit}".rstrip())
            parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "Данных о пациенте в базе нет."


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
        f"КОНТЕКСТ ПАЦИЕНТА:\n{_patient_context(user_id)}\n\n"
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
        {"role": "system", "content": SYSTEM_PROMPT},
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


def _chat(client, model: str, messages: list[dict], num_predict: int, think: bool | None = None):
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


def _split_message(response) -> tuple[str, str]:
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
