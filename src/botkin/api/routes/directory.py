"""API веб-кабинета: справочник препаратов (ГРЛС).

Автодополнение в формах: ввод названия препарата → топ совпадений.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from botkin.db.connection import get_conn

from ..deps import get_user_id

router = APIRouter(prefix="/api/directory", tags=["directory"])


@router.get("/drugs")
def search_drugs(
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(10, ge=1, le=50),
    user_id: int = Depends(get_user_id),
) -> list[dict]:
    """Поиск препаратов по первым буквам названия в индексе ГРЛС (rag_chunks).

    Возвращает [{name, type, mnn, statuses, ref_key}].
    type: trade (торговое), mnn (МНН), both.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ref_key, text, meta_json
               FROM rag_chunks
               WHERE source = 'drugs'
                 AND lower(ref_key) LIKE lower('drug:' || ? || '%')
               ORDER BY ref_key
               LIMIT ?""",
            (q, limit),
        ).fetchall()
    results: list[dict] = []
    for r in rows:
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        results.append({
            "name": meta.get("name", r["ref_key"].replace("drug:", "")),
            "type": meta.get("type", ""),
            "mnn": meta.get("mnn", ""),
            "statuses": meta.get("statuses", []),
            "ref_key": r["ref_key"],
        })
    return results
