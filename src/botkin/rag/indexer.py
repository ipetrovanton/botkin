"""Индексация RAG: справочники ГРЛС/ФСЛИ + недельные health-сводки пациента.

Каждая запись справочника → один текстовый чанк на русском (эмбеддер видит
человекочитаемое описание, а не голый JSON). Health-данные сворачиваются в
недельные сводки: точек много (720 пульсов/день), но для рекомендаций важны
агрегаты и тренды, а не сырой ряд.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from botkin.db.repos import HealthRepo
from botkin.normalize.base import read_registry
from botkin.rag import store
from botkin.rag.embeddings import embed_texts

log = logging.getLogger(__name__)

_REFERENCE_DIR = Path(__file__).parent.parent / "reference"

_STATUS_RU = {
    "active": "активен",
    "excluded": "исключён из реестра",
    "suspended": "обращение приостановлено",
    "modified": "запись изменялась",
}

_METRIC_RU = {
    "resting_heart_rate": "пульс покоя",
    "heart_rate": "пульс",
    "steps": "шаги",
    "sleep_seconds": "сон",
    "stress_avg": "средний стресс",
    "body_battery_max": "body battery (макс)",
    "weight_kg": "вес",
    "blood_pressure_systolic": "систолическое давление",
    "blood_pressure_diastolic": "диастолическое давление",
    "hrv_last_night": "ночная вариабельность пульса (HRV)",
    "spo2_avg": "насыщение крови кислородом (SpO2)",
}


def drug_chunk(rec: dict) -> dict:
    """ГРЛС-запись → чанк. type: trade (торговое), mnn (МНН), both."""
    name = rec["name"]
    parts = []
    if rec.get("type") == "trade":
        parts.append(f"Лекарственный препарат «{name}» (торговое название)")
    elif rec.get("type") == "mnn":
        parts.append(f"Действующее вещество (МНН) «{name}»")
    else:
        parts.append(f"Лекарственный препарат «{name}»")
    if rec.get("mnn"):
        parts.append(f"действующее вещество: {rec['mnn']}")
    statuses = [_STATUS_RU.get(s, s) for s in rec.get("statuses", []) if s != "modified"]
    if statuses:
        parts.append(f"статус в реестре ГРЛС: {', '.join(statuses)}")
    return {"ref_key": f"drug:{name.lower()}", "text": ". ".join(parts) + ".", "meta": rec}


def analyte_chunk(rec: dict) -> dict:
    """ФСЛИ-запись → чанк: имя, синонимы, единицы, группа."""
    name = rec["name"]
    parts = [f"Лабораторный показатель «{name}»"]
    if rec.get("group"):
        parts.append(f"группа: {rec['group']}")
    syns = [s for s in rec.get("synonyms", []) if s.lower() != name.lower()]
    if syns:
        parts.append(f"синонимы: {', '.join(syns[:8])}")
    if rec.get("units"):
        parts.append(f"единицы измерения: {', '.join(rec['units'])}")
    return {"ref_key": f"analyte:{name.lower()}", "text": ". ".join(parts) + ".", "meta": rec}


def _fmt_value(metric: str, row: dict) -> str:
    if metric == "sleep_seconds":
        return f"в среднем {row['avg'] / 3600:.1f} ч"
    unit = row.get("unit") or ""
    return f"среднее {row['avg']:g}{(' ' + unit) if unit else ''} (мин {row['min']:g}, макс {row['max']:g})"


def health_week_chunks(repo: HealthRepo, weeks: int = 8) -> list[dict]:
    """Недельные сводки метрик пациента за последние N недель."""
    today = dt.date.today()
    chunks: list[dict] = []
    for w in range(weeks):
        end = today - dt.timedelta(days=7 * w)
        start = end - dt.timedelta(days=6)
        daily = repo.daily_summary(str(start), str(end) + " 23:59:59")
        if not daily:
            continue
        by_metric: dict[str, list[dict]] = {}
        for row in daily:
            by_metric.setdefault(row["metric"], []).append(row)
        lines = [f"Данные носимых устройств пациента за неделю {start} — {end}:"]
        for metric, rows in sorted(by_metric.items()):
            label = _METRIC_RU.get(metric, metric)
            avg_of_avg = sum(r["avg"] for r in rows) / len(rows)
            lo = min(r["min"] for r in rows if r["min"] is not None)
            hi = max(r["max"] for r in rows if r["max"] is not None)
            if metric == "sleep_seconds":
                lines.append(f"- {label}: в среднем {avg_of_avg / 3600:.1f} ч в сутки")
            elif metric == "steps":
                lines.append(f"- {label}: в среднем {avg_of_avg:.0f} в день")
            else:
                unit = rows[0].get("unit") or ""
                lines.append(
                    f"- {label}: среднее {avg_of_avg:.1f}{(' ' + unit) if unit else ''},"
                    f" диапазон {lo:g}–{hi:g}"
                )
        iso_year, iso_week, _ = end.isocalendar()
        chunks.append({
            "ref_key": f"health:{repo.user_id}:{iso_year}-W{iso_week:02d}",
            "text": "\n".join(lines),
            "user_id": repo.user_id,
            "meta": {"date_from": str(start), "date_to": str(end)},
        })
    return chunks


def index_registries(progress: bool = True) -> dict:
    """Полная (пере)индексация обоих справочников. Возвращает счётчики."""
    drugs = [drug_chunk(r) for r in read_registry(_REFERENCE_DIR / "drugs" / "registry.jsonl")]
    analytes = [
        analyte_chunk(r) for r in read_registry(_REFERENCE_DIR / "analytes" / "registry.jsonl")
    ]
    counts = {}
    with store.vec_conn() as conn:
        for source, items in (("drugs", drugs), ("analytes", analytes)):
            log.info("Индексация %s: %d чанков", source, len(items))
            embeddings = embed_texts([c["text"] for c in items])
            counts[source] = store.upsert_chunks(conn, source, items, embeddings)
    return counts


def index_health(user_id: int, weeks: int = 8) -> int:
    """(Пере)индексация health-сводок пользователя. Вызывается после синка."""
    with store.vec_conn() as conn:
        repo = HealthRepo(conn, user_id)
        chunks = health_week_chunks(repo, weeks)
        if not chunks:
            return 0
        embeddings = embed_texts([c["text"] for c in chunks])
        return store.upsert_chunks(conn, "health", chunks, embeddings)
