"""Семантический поиск по RAG-индексу: запрос → эмбеддинг → KNN → чанки."""
from __future__ import annotations

from botkin.config import RAG_TOP_K
from botkin.rag import store
from botkin.rag.embeddings import embed_query


def search(
    query: str,
    *,
    sources: list[str] | None = None,
    user_id: int | None = None,
    top_k: int = RAG_TOP_K,
) -> list[dict]:
    """Топ-k чанков по смысловой близости. sources ограничивает корпус
    ('drugs' | 'analytes' | 'health'), user_id открывает приватные health-чанки."""
    embedding = embed_query(query)
    with store.vec_conn() as conn:
        return store.search(
            conn, embedding, sources=sources, user_id=user_id, top_k=top_k,
        )
