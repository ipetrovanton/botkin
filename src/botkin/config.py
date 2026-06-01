"""Единая точка конфигурации приложения.

Приоритет источников:
1. Переменные окружения (из .env через python-dotenv)
2. config.json в корне проекта
3. Жёстко заданные значения по умолчанию
"""
import json
import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).parent.parent.parent
    load_dotenv(_project_root / ".env")
except ImportError:
    _project_root = Path(__file__).parent.parent.parent

log = logging.getLogger("botkin.config")

CONFIG_PATH = _project_root / "config.json"

_DEFAULTS: dict = {
    "vlm": {
        "model": "qwen3-vl:8b",
        "temperature": 0.0,
        "num_ctx": 32768,
        "max_tokens": 8192,
        "repeat_penalty": 1.2,
    },
    "pdf_to_image": {
        "scale_x": 2.0,
        "scale_y": 2.0,
        "max_pages": 50,
    },
    "database": {
        "sqlite_path": "./data/botkin.db",
    },
    "bot": {
        "polling_timeout": 30,
        "api_url": "http://localhost:8000",
    },
    "upload": {
        "max_bytes": 20 * 1024 * 1024,
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"],
        "sources_dir": "./sources",
    },
}


def _load_json_config() -> dict:
    if not CONFIG_PATH.exists():
        log.warning("config.json не найден по пути %s, используются значения по умолчанию", CONFIG_PATH)
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Ошибка загрузки config.json: %s", e)
        return {}


_json = _load_json_config()


def _get(key_path: str, default=None):
    parts = key_path.split(".")
    value = _json
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
        if value is None:
            return default
    return value


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (_project_root / p)


# ── VLM ──────────────────────────────────────────────────────────────────────
VLM_MODEL = os.getenv("VLM_MODEL", _get("vlm.model", _DEFAULTS["vlm"]["model"]))
VLM_TEMPERATURE = float(os.getenv("VLM_TEMPERATURE", _get("vlm.temperature", _DEFAULTS["vlm"]["temperature"])))
VLM_NUM_CTX = int(os.getenv("VLM_NUM_CTX", _get("vlm.num_ctx", _DEFAULTS["vlm"]["num_ctx"])))
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", _get("vlm.max_tokens", _DEFAULTS["vlm"]["max_tokens"])))
VLM_REPEAT_PENALTY = float(os.getenv("VLM_REPEAT_PENALTY", _get("vlm.repeat_penalty", _DEFAULTS["vlm"]["repeat_penalty"])))

# ── Ollama ────────────────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# ── PDF → изображение ─────────────────────────────────────────────────────────
PDF_SCALE_X = float(_get("pdf_to_image.scale_x", _DEFAULTS["pdf_to_image"]["scale_x"]))
PDF_SCALE_Y = float(_get("pdf_to_image.scale_y", _DEFAULTS["pdf_to_image"]["scale_y"]))
MAX_PAGES = int(_get("pdf_to_image.max_pages", _DEFAULTS["pdf_to_image"]["max_pages"]))

# ── База данных ───────────────────────────────────────────────────────────────
SQLITE_PATH = str(_resolve_path(os.getenv("SQLITE_PATH", _get("database.sqlite_path", _DEFAULTS["database"]["sqlite_path"]))))

# ── Telegram бот ──────────────────────────────────────────────────────────────
BOT_POLLING_TIMEOUT = int(_get("bot.polling_timeout", _DEFAULTS["bot"]["polling_timeout"]))
BOT_API_URL = os.getenv("API_URL", _get("bot.api_url", _DEFAULTS["bot"]["api_url"]))

# ── Загрузка файлов ───────────────────────────────────────────────────────────
UPLOAD_MAX_BYTES = int(_get("upload.max_bytes", _DEFAULTS["upload"]["max_bytes"]))
UPLOAD_ALLOWED_EXTENSIONS: set[str] = set(_get("upload.allowed_extensions", _DEFAULTS["upload"]["allowed_extensions"]))
UPLOAD_SOURCES_DIR = _resolve_path(os.getenv("SOURCES_DIR", _get("upload.sources_dir", _DEFAULTS["upload"]["sources_dir"])))