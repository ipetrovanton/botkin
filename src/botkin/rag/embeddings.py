"""Эмбеддинги через Ollama /api/embed (bge-m3, 1024-dim).

Ollama сам батчит вход, но мы режем на RAG_EMBED_BATCH: один гигантский запрос
на 27 тыс. строк держит HTTP-соединение минуты и падает целиком при любом сбое,
а батчи дают прогресс и точку ретрая.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from botkin.config import OLLAMA_KEEP_ALIVE, RAG_EMBED_BATCH, RAG_EMBED_MODEL
from botkin.llm.client import _detect_ollama_url

log = logging.getLogger(__name__)

EMBED_TIMEOUT = 300.0


def embed_texts(texts: list[str], model: str = RAG_EMBED_MODEL) -> list[list[float]]:
    """Вектор на каждую строку. Порядок сохраняется. Пустой вход → пустой выход."""
    if not texts:
        return []
    url = f"{_detect_ollama_url()}/api/embed"
    out: list[list[float]] = []
    for i in range(0, len(texts), RAG_EMBED_BATCH):
        batch = texts[i:i + RAG_EMBED_BATCH]
        payload = json.dumps(
            {"model": model, "input": batch, "keep_alive": OLLAMA_KEEP_ALIVE}
        ).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as resp:
            data = json.load(resp)
        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(batch):
            raise RuntimeError(
                f"Ollama embed вернул {len(embeddings)} векторов на {len(batch)} строк"
            )
        out.extend(embeddings)
        if len(texts) > RAG_EMBED_BATCH:
            log.info("Эмбеддинг: %d/%d", min(i + RAG_EMBED_BATCH, len(texts)), len(texts))
    return out


def embed_query(text: str, model: str = RAG_EMBED_MODEL) -> list[float]:
    return embed_texts([text], model)[0]
