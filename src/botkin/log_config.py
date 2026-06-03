"""Единая настройка логирования для всех точек входа (API, бот).

Настраивает логгер `botkin` (родитель всех `botkin.*`), не трогая логгеры uvicorn.
Уровень берётся из переменной окружения LOG_LEVEL (по умолчанию INFO). Для отладки
pipeline — `LOG_LEVEL=DEBUG`: тогда логируется сырой ответ VLM и размеры изображений.
"""
from __future__ import annotations

import logging
import os

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger("botkin")
    logger.setLevel(getattr(logging, level, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    logger.propagate = False
