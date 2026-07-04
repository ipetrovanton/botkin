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
    RAG_RECOMMEND_NUM_PREDICT, RAG_TOP_K,
)
from botkin.db.connection import get_conn
from botkin.db.repos import HealthRepo
from botkin.llm.client import get_raw_client
from botkin.rag import retriever

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
5. Завершай ответ напоминанием, что окончательные решения принимает лечащий врач."""

_RECENT_LABS_SQL = """
    SELECT COALESCE(analyte_canonical, analyte_name) AS name, value_num, unit,
           ref_low, ref_high, taken_at
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
            lines = ["Отклонения в анализах пациента (свежие):"]
            for r in labs:
                ref = f"норма {r['ref_low']:g}–{r['ref_high']:g}" if r["ref_low"] is not None \
                    and r["ref_high"] is not None else f"норма до {r['ref_high']:g}" \
                    if r["ref_high"] is not None else f"норма от {r['ref_low']:g}"
                lines.append(
                    f"- {r['name']}: {r['value_num']:g} {r['unit'] or ''} ({ref}), {r['taken_at']}"
                )
            parts.append("\n".join(lines))

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


def recommend(user_id: int, question: str, *, top_k: int = RAG_TOP_K) -> dict:
    """Ответ на вопрос пациента с RAG-контекстом. Возвращает text + использованные чанки."""
    chunks = retriever.search(question, user_id=user_id, top_k=top_k)
    med_names = _extract_med_mentions(user_id)
    for name in med_names[:5]:
        extra = retriever.search(name, sources=["drugs"], user_id=user_id, top_k=2)
        seen = {c["ref_key"] for c in chunks}
        chunks.extend(c for c in extra if c["ref_key"] not in seen)

    context_blocks = [f"[{c['source']}] {c['text']}" for c in chunks]
    user_msg = (
        f"КОНТЕКСТ ПАЦИЕНТА:\n{_patient_context(user_id)}\n\n"
        f"ВЫДЕРЖКИ ИЗ СПРАВОЧНИКОВ И ДАННЫХ:\n" + "\n\n".join(context_blocks) +
        f"\n\nВОПРОС ПАЦИЕНТА: {question}"
    )
    client = get_raw_client(timeout=300.0)
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=RAG_RECOMMEND_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=RAG_RECOMMEND_NUM_PREDICT,
        extra_body={"options": {
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "num_ctx": RAG_RECOMMEND_NUM_CTX,
            "num_predict": RAG_RECOMMEND_NUM_PREDICT,
            "temperature": 0.3,
        }},
    )
    text = (response.choices[0].message.content or "").strip()
    log.info("Рекомендация за %.1fs, чанков в контексте: %d",
             time.perf_counter() - t0, len(chunks))
    return {
        "answer": text,
        "chunks": [
            {"source": c["source"], "ref_key": c["ref_key"], "distance": c["distance"]}
            for c in chunks
        ],
    }


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
