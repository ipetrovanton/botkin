"""API веб-кабинета: RAG — поиск по справочникам и рекомендации LLM."""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from botkin.rag import retriever, store

from ..deps import get_user_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["rag"])

_reindex_state: dict = {"state": "idle"}
_reindex_lock = threading.Lock()


class RecommendRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


@router.get("/status")
def status() -> dict:
    with store.vec_conn() as conn:
        stats = store.index_stats(conn)
    with _reindex_lock:
        return {**stats, "reindex": dict(_reindex_state)}


def _run_reindex() -> None:
    global _reindex_state
    try:
        from botkin.rag.indexer import index_registries
        counts = index_registries()
        with _reindex_lock:
            _reindex_state = {"state": "done", **counts}
        log.info("RAG-реиндексация справочников: %s", counts)
    except Exception as e:
        log.exception("RAG-реиндексация упала")
        with _reindex_lock:
            _reindex_state = {"state": "error", "error": str(e)[:300]}


@router.post("/reindex")
def reindex(background_tasks: BackgroundTasks) -> dict:
    """Полная (пере)индексация справочников ГРЛС+ФСЛИ (~27 тыс. чанков, минуты)."""
    global _reindex_state
    with _reindex_lock:
        if _reindex_state.get("state") == "running":
            raise HTTPException(status_code=409, detail="Индексация уже идёт")
        _reindex_state = {"state": "running"}
    background_tasks.add_task(_run_reindex)
    return {"status": "started"}


@router.get("/search")
def search(
    q: str = Query(..., min_length=2),
    sources: str | None = Query(None, description="через запятую: drugs,analytes,health"),
    top_k: int = Query(8, ge=1, le=30),
    user_id: int = Depends(get_user_id),
) -> dict:
    source_list = [s.strip() for s in sources.split(",")] if sources else None
    items = retriever.search(q, sources=source_list, user_id=user_id, top_k=top_k)
    return {"query": q, "items": items}


@router.post("/recommend")
def recommend(req: RecommendRequest, user_id: int = Depends(get_user_id)) -> dict:
    """Ответ ассистента с контекстом из справочников, анализов и health-данных."""
    from botkin.rag.recommend import recommend as _recommend
    try:
        return _recommend(user_id, req.question)
    except Exception as e:
        log.exception("Рекомендация не удалась")
        raise HTTPException(status_code=502, detail=f"LLM недоступна: {e}")
