"""Векторное хранилище на sqlite-vec поверх основной botkin.db.

Чанки (текст + метаданные) живут в обычной таблице rag_chunks (schema.sql),
вектора — в виртуальной vec0-таблице rag_vectors, которую создаёт этот модуль:
sqlite-vec — загружаемое расширение, и schema.sql, исполняемый в init_db без
расширения, о vec0 знать не должен.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

import sqlite_vec

from botkin.db.connection import get_conn, transaction

DIM = 1024  # bge-m3


@contextmanager
def vec_conn() -> Iterator[sqlite3.Connection]:
    """Коннект к основной БД с загруженным расширением sqlite-vec."""
    with get_conn() as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS rag_vectors "
            f"USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{DIM}])"
        )
        yield conn


def upsert_chunks(
    conn: sqlite3.Connection,
    source: str,
    items: list[dict],
    embeddings: list[list[float]],
) -> int:
    """Записывает чанки и их вектора. item: {ref_key, text, meta?, user_id?}.

    Идемпотентно по (source, ref_key): существующий чанк обновляется вместе с вектором.
    """
    if len(items) != len(embeddings):
        raise ValueError(f"{len(items)} чанков и {len(embeddings)} векторов")
    with transaction(conn):
        for item, emb in zip(items, embeddings):
            cur = conn.execute(
                """INSERT INTO rag_chunks(source, user_id, ref_key, text, meta_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, ref_key) DO UPDATE SET
                       text = excluded.text, meta_json = excluded.meta_json,
                       user_id = excluded.user_id
                   RETURNING id""",
                (source, item.get("user_id"), item["ref_key"], item["text"],
                 json.dumps(item.get("meta"), ensure_ascii=False) if item.get("meta") else None),
            )
            chunk_id = cur.fetchone()[0]
            blob = sqlite_vec.serialize_float32(emb)
            # vec0 не поддерживает ON CONFLICT — delete+insert.
            conn.execute("DELETE FROM rag_vectors WHERE chunk_id = ?", (chunk_id,))
            conn.execute(
                "INSERT INTO rag_vectors(chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, blob),
            )
    return len(items)


def search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    sources: list[str] | None = None,
    user_id: int | None = None,
    top_k: int = 8,
) -> list[dict]:
    """KNN по векторам с гидрацией чанков.

    Приватные чанки (health) видны только своему пользователю; общие справочники
    (user_id IS NULL) — всем. Фильтр по source — после KNN, поэтому k берём с запасом.
    """
    blob = sqlite_vec.serialize_float32(query_embedding)
    rows = conn.execute(
        "SELECT chunk_id, distance FROM rag_vectors WHERE embedding MATCH ? AND k = ? "
        "ORDER BY distance",
        (blob, top_k * 5),
    ).fetchall()
    results: list[dict] = []
    for r in rows:
        chunk = conn.execute(
            "SELECT id, source, user_id, ref_key, text, meta_json FROM rag_chunks WHERE id = ?",
            (r["chunk_id"],),
        ).fetchone()
        if chunk is None:
            continue
        if sources and chunk["source"] not in sources:
            continue
        if chunk["user_id"] is not None and chunk["user_id"] != user_id:
            continue
        results.append({
            "source": chunk["source"],
            "ref_key": chunk["ref_key"],
            "text": chunk["text"],
            "meta": json.loads(chunk["meta_json"]) if chunk["meta_json"] else None,
            "distance": r["distance"],
        })
        if len(results) >= top_k:
            break
    return results


def index_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT source, COUNT(*) AS c FROM rag_chunks GROUP BY source"
    ).fetchall()
    vectors = conn.execute("SELECT COUNT(*) AS c FROM rag_vectors").fetchone()["c"]
    return {"chunks": {r["source"]: r["c"] for r in rows}, "vectors": vectors}
