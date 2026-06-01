"""Централизованная конфигурация приложения.

Источники (по приоритету):
1. Переменные окружения (через python-dotenv из .env)
2. config.json в корне botkin-core
3. Жёстко заданные значения по умолчанию (в этом файле)
"""
import json
import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("botkin.config")

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# ---------------------------------------------------------------------------
# Значения по умолчанию
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG: dict = {
    "vlm": {
        "model": "qwen3-vl:3b",
        "temperature": 0.0,
        "num_ctx": 8192,
        "num_predict": 2048,
        "max_tokens": 4096,
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
        "allowed_extensions": {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"},
        "sources_dir": "./sources",
    },
}


def _load_json_config() -> dict:
    if not CONFIG_PATH.exists():
        log.warning("config.json не найден по пути %s, используются значения по умолчанию", CONFIG_PATH)
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info("Конфигурация загружена из %s", CONFIG_PATH)
        return data
    except json.JSONDecodeError as e:
        log.error("config.json содержит невалидный JSON: %s", e)
        return {}
    except Exception as e:
        log.error("Ошибка загрузки config.json: %s", e)
        return {}


_json_config = _load_json_config()


def _get(key_path: str, default=None):
    """Извлекает значение из конфига по точечному пути 'vlm.model'."""
    parts = key_path.split(".")
    value = _json_config
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
        if value is None:
            return default
    return value


# ===========================================================================
# VLM (qwen3-vl)
# ===========================================================================
VLM_MODEL = os.getenv("VLM_MODEL", _get("vlm.model", _DEFAULT_CONFIG["vlm"]["model"]))
VLM_TEMPERATURE = float(os.getenv("VLM_TEMPERATURE", _get("vlm.temperature", _DEFAULT_CONFIG["vlm"]["temperature"])))
VLM_NUM_CTX = int(os.getenv("VLM_NUM_CTX", _get("vlm.num_ctx", _DEFAULT_CONFIG["vlm"]["num_ctx"])))
VLM_NUM_PREDICT = int(os.getenv("VLM_NUM_PREDICT", _get("vlm.num_predict", _DEFAULT_CONFIG["vlm"]["num_predict"])))
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", _get("vlm.max_tokens", _DEFAULT_CONFIG["vlm"]["max_tokens"])))
VLM_REPEAT_PENALTY = float(os.getenv("VLM_REPEAT_PENALTY", _get("vlm.repeat_penalty", _DEFAULT_CONFIG["vlm"]["repeat_penalty"])))

# ===========================================================================
# PDF → изображение
# ===========================================================================
PDF_SCALE_X = float(_get("pdf_to_image.scale_x", _DEFAULT_CONFIG["pdf_to_image"]["scale_x"]))
PDF_SCALE_Y = float(_get("pdf_to_image.scale_y", _DEFAULT_CONFIG["pdf_to_image"]["scale_y"]))
MAX_PAGES = int(_get("pdf_to_image.max_pages", _DEFAULT_CONFIG["pdf_to_image"]["max_pages"]))

# ===========================================================================
# База данных
# ===========================================================================
_sqlite_path = os.getenv("SQLITE_PATH", _get("database.sqlite_path", _DEFAULT_CONFIG["database"]["sqlite_path"]))
if not os.path.isabs(_sqlite_path):
    _sqlite_path = str(Path(__file__).parent.parent / _sqlite_path)
SQLITE_PATH = _sqlite_path

# ===========================================================================
# Telegram бот
# ===========================================================================
BOT_POLLING_TIMEOUT = int(_get("bot.polling_timeout", _DEFAULT_CONFIG["bot"]["polling_timeout"]))
BOT_API_URL = os.getenv("API_URL", _get("bot.api_url", _DEFAULT_CONFIG["bot"]["api_url"]))

# ===========================================================================
# Загрузка файлов
# ===========================================================================
UPLOAD_MAX_BYTES = int(_get("upload.max_bytes", _DEFAULT_CONFIG["upload"]["max_bytes"]))
UPLOAD_ALLOWED_EXTENSIONS: set[str] = set(
    _get("upload.allowed_extensions", _DEFAULT_CONFIG["upload"]["allowed_extensions"])
)
UPLOAD_SOURCES_DIR = Path(
    os.getenv("SOURCES_DIR", _get("upload.sources_dir", _DEFAULT_CONFIG["upload"]["sources_dir"]))
)
if not UPLOAD_SOURCES_DIR.is_absolute():
    UPLOAD_SOURCES_DIR = Path(__file__).parent.parent / UPLOAD_SOURCES_DIR