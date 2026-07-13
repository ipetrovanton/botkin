"""Автономное обновление медицинского research-RAG из PubMed.

Тянет свежие публикации по темам из config (rag.research.topics) и (пере)индексирует
их в векторный индекс как source="research". Идемпотентно по PMID — можно гонять по
расписанию (Windows Task Scheduler / cron), модель будет опираться на актуальные статьи.

Запуск:
  uv run python -m scripts.update_medical_research                 # темы из config
  uv run python -m scripts.update_medical_research --topic "sepsis biomarkers 2026"
  uv run python -m scripts.update_medical_research --per-topic 25
"""
from __future__ import annotations

import argparse
import json

from botkin.db.connection import init_db
from botkin.rag.research import index_research


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", action="append", dest="topics",
                    help="Тема (можно повторять). Без флага — темы из config.")
    ap.add_argument("--per-topic", type=int, default=None)
    args = ap.parse_args()
    init_db()
    result = index_research(topics=args.topics, per_topic=args.per_topic)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
