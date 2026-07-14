"""Сравнение embedding-моделей для RAG: качество поиска на тестовом корпусе.

Бенчмарк:
1. Фиксированный набор запросов с ожидаемыми релевантными чанками (golden set).
2. Для каждой модели: эмбеддинг запроса → KNN → проверка, попал ли ожидаемый чанк в top-k.
3. Метрики: hit_rate@k (доля запросов, где релевантный чанк в top-k), MRR (mean reciprocal rank).

Модели сравниваются на одном и том же корпусе, загруженном в БД. Для каждой модели
строится отдельный временный векторный индекс — размерности могут отличаться.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

import sqlite_vec

from botkin.db.connection import get_conn
from botkin.rag.embeddings import embed_texts

log = logging.getLogger(__name__)


@dataclass
class BenchmarkQuery:
    query: str
    expected_ref_keys: list[str]  # ref_key чанков, которые должны быть в top-k


@dataclass
class ModelResult:
    model: str
    hit_rate: float       # доля запросов, где хотя бы один ожидаемый чанк в top-k
    mrr: float            # mean reciprocal rank
    avg_distance: float   # среднее расстояние до top-1
    per_query: list[dict] = field(default_factory=list)


# Тестовый корпус: медицинские запросы → ожидаемые справочные чанки
# ref_keys соответствуют тем, что indexer кладёт в rag_chunks
_GOLDEN_SET: list[BenchmarkQuery] = [
    BenchmarkQuery(
        "парацетамол дозировка для детей",
        ["drug:paracetamol", "drug:парацетамол"],
    ),
    BenchmarkQuery(
        "повышенный гемоглобин причины",
        ["analyte:гемоглобин", "analyte:hemoglobin"],
    ),
    BenchmarkQuery(
        "противовоспалительное средство",
        ["drug:ibuprofen", "drug:ибупрофен", "drug:nimesulide"],
    ),
    BenchmarkQuery(
        "лейкоциты в крови норма",
        ["analyte:лейкоциты", "analyte:leukocytes", "analyte:wbc"],
    ),
    BenchmarkQuery(
        "антибиотик широкого спектра",
        ["drug:amoxicillin", "drug:амоксициллин", "drug:azithromycin"],
    ),
]


def _get_corpus() -> list[dict]:
    """Загружает все чанки из rag_chunks (без векторов) — текст для эмбеддинга."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, source, ref_key, text FROM rag_chunks ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def _build_temp_index(
    conn: sqlite3.Connection, model: str, corpus: list[dict], dim: int,
) -> str:
    """Создаёт временную vec0-таблицу с векторами модели. Возвращает имя таблицы."""
    table = f"_bench_{model.replace(':', '_').replace('-', '_').replace('.', '_')}"
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(
        f"CREATE VIRTUAL TABLE {table} USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{dim}])"
    )
    texts = [c["text"] for c in corpus]
    embeddings = embed_texts(texts, model=model)
    for chunk, emb in zip(corpus, embeddings):
        blob = sqlite_vec.serialize_float32(emb)
        conn.execute(
            f"INSERT INTO {table}(chunk_id, embedding) VALUES (?, ?)",
            (chunk["id"], blob),
        )
    return table


def _search_temp(
    conn: sqlite3.Connection, table: str, query_emb: list[float], top_k: int = 10,
) -> list[dict]:
    """KNN по временной таблице → [{id, source, ref_key, distance}]."""
    blob = sqlite_vec.serialize_float32(query_emb)
    rows = conn.execute(
        f"SELECT v.chunk_id, v.distance, c.source, c.ref_key "
        f"FROM {table} AS v JOIN rag_chunks AS c ON v.chunk_id = c.id "
        f"WHERE v.embedding MATCH ? AND k = ? "
        f"ORDER BY v.distance",
        (blob, top_k),
    ).fetchall()
    return [dict(r) for r in rows]


def run_benchmark(
    models: list[str],
    queries: list[BenchmarkQuery] | None = None,
    top_k: int = 10,
) -> list[ModelResult]:
    """Сравнивает embedding-модели на golden set. Возвращает результаты по каждой.

    Требует запущенную Ollama с установленными моделями (ollama pull <model>).
    Корпус берётся из текущего rag_chunks (справочники ГРЛС/ФСЛИ + research).
    """
    queries = queries or _GOLDEN_SET
    corpus = _get_corpus()
    if not corpus:
        log.warning("Бенчмарк: корпус пуст (rag_chunks не содержит данных)")
        return []

    results: list[ModelResult] = []
    for model in models:
        log.info("Бенчмарк embedding-модели: %s", model)
        try:
            # Определяем размерность модели одним эмбеддингом
            probe = embed_texts(["test"], model=model)[0]
            dim = len(probe)
        except Exception as e:
            log.error("Модель %s недоступна: %s", model, e)
            results.append(ModelResult(model=model, hit_rate=0.0, mrr=0.0, avg_distance=0.0))
            continue

        with get_conn() as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            table = _build_temp_index(conn, model, corpus, dim)

            per_query: list[dict] = []
            hits = 0
            rr_sum = 0.0
            dist_sum = 0.0
            for q in queries:
                q_emb = embed_texts([q.query], model=model)[0]
                found = _search_temp(conn, table, q_emb, top_k)
                if not found:
                    per_query.append({"query": q.query, "hit": False, "rank": None})
                    continue
                dist_sum += found[0]["distance"]
                # Ищем первый релевантный чанк
                hit = False
                rank = None
                for i, r in enumerate(found, 1):
                    if r["ref_key"] in q.expected_ref_keys:
                        hit = True
                        rank = i
                        break
                if hit:
                    hits += 1
                    rr_sum += 1.0 / rank
                per_query.append({
                    "query": q.query,
                    "hit": hit,
                    "rank": rank,
                    "top1_ref_key": found[0]["ref_key"],
                    "top1_distance": round(found[0]["distance"], 4),
                })

            conn.execute(f"DROP TABLE IF EXISTS {table}")

            n = len(queries)
            result = ModelResult(
                model=model,
                hit_rate=hits / n if n else 0.0,
                mrr=rr_sum / n if n else 0.0,
                avg_distance=dist_sum / n if n else 0.0,
                per_query=per_query,
            )
            results.append(result)
            log.info(
                "%s: hit_rate=%.2f, MRR=%.3f, avg_dist=%.4f",
                model, result.hit_rate, result.mrr, result.avg_distance,
            )

    return results


def format_results(results: list[ModelResult]) -> str:
    """Текстовая сводка для вывода в чат/лог."""
    lines = ["Бенчмарк embedding-моделей:", ""]
    for r in results:
        lines.append(
            f"  {r.model}: hit_rate={r.hit_rate:.2f}, MRR={r.mrr:.3f}, "
            f"avg_dist={r.avg_distance:.4f}"
        )
    return "\n".join(lines)
