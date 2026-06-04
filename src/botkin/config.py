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
        "model": "qwen3-vl:8b-instruct",
        "temperature": 0.0,
        "num_ctx": 16384,
        "max_tokens": 8192,
        "num_predict": 8192,
        "repeat_penalty": 1.2,
    },
    "ollama": {
        "keep_alive": "30m",
    },
    "pdf_to_image": {
        "render_dpi": 200,
        "max_pages": 50,
    },
    "image": {
        "extract_long_side": 2200,
        "jpeg_quality": 90,
        "classify_long_side": 1000,
        "clahe_clip": 2.0,
        "unsharp_amount": 1.5,
        "deskew_min_angle": 3.0,
        "deskew_min_area": 0.40,
        "deskew_max_area": 0.97,
        "lowres_warn": 1500,
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
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"],
        "sources_dir": "./sources",
    },
    "drugs": {
        "max_edit_ratio": 0.40,
        "ratio_floor": 70,
    },
    "analytes": {
        "max_edit_ratio": 0.35,
        "ratio_floor": 75,
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
VLM_NUM_PREDICT = int(os.getenv("VLM_NUM_PREDICT", _get("vlm.num_predict", _DEFAULTS["vlm"]["num_predict"])))
VLM_REPEAT_PENALTY = float(os.getenv("VLM_REPEAT_PENALTY", _get("vlm.repeat_penalty", _DEFAULTS["vlm"]["repeat_penalty"])))
# Потолок одного VLM-вызова. Деградировавший вызов (генерация дублей) не должен висеть
# минутами — по таймауту прерываем, страница пропускается, документ сохраняет остальное.
VLM_REQUEST_TIMEOUT = float(os.getenv("VLM_REQUEST_TIMEOUT", "120"))

# ── Ollama ────────────────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# keep_alive держит модель в VRAM между вызовами — нет перезагрузки весов 6 ГБ
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", _get("ollama.keep_alive", _DEFAULTS["ollama"]["keep_alive"]))

# ── PDF → изображение ─────────────────────────────────────────────────────────
PDF_RENDER_DPI = int(_get("pdf_to_image.render_dpi", _DEFAULTS["pdf_to_image"]["render_dpi"]))
MAX_PAGES = int(_get("pdf_to_image.max_pages", _DEFAULTS["pdf_to_image"]["max_pages"]))

# ── Подготовка изображений ────────────────────────────────────────────────────
IMAGE_EXTRACT_LONG_SIDE = int(_get("image.extract_long_side", _DEFAULTS["image"]["extract_long_side"]))
IMAGE_JPEG_QUALITY = int(_get("image.jpeg_quality", _DEFAULTS["image"]["jpeg_quality"]))
IMAGE_CLASSIFY_LONG_SIDE = int(_get("image.classify_long_side", _DEFAULTS["image"]["classify_long_side"]))
IMAGE_CLAHE_CLIP = float(_get("image.clahe_clip", _DEFAULTS["image"]["clahe_clip"]))
IMAGE_UNSHARP_AMOUNT = float(_get("image.unsharp_amount", _DEFAULTS["image"]["unsharp_amount"]))
IMAGE_DESKEW_MIN_ANGLE = float(_get("image.deskew_min_angle", _DEFAULTS["image"]["deskew_min_angle"]))
IMAGE_DESKEW_MIN_AREA = float(_get("image.deskew_min_area", _DEFAULTS["image"]["deskew_min_area"]))
IMAGE_DESKEW_MAX_AREA = float(_get("image.deskew_max_area", _DEFAULTS["image"]["deskew_max_area"]))
PHOTO_LOWRES_WARN = int(_get("image.lowres_warn", _DEFAULTS["image"]["lowres_warn"]))

# ── Нормализация лекарств ─────────────────────────────────────────────────────
# Scorer = дистанция Дамерау-Левенштейна (выбран по замеру на словаре 20 948, см. спек):
# cap = max(1, floor(len(имя) * DRUG_MAX_EDIT_RATIO)); фильтр fuzz.ratio ≥ DRUG_RATIO_FLOOR.
DRUG_MAX_EDIT_RATIO = float(_get("drugs.max_edit_ratio", _DEFAULTS["drugs"]["max_edit_ratio"]))
DRUG_RATIO_FLOOR = float(_get("drugs.ratio_floor", _DEFAULTS["drugs"]["ratio_floor"]))

# ── Нормализация анализов (ФСЛИ) ──────────────────────────────────────────────
# Аналогично препаратам: cap по дистанции Дамерау-Левенштейна + ratio-floor.
ANALYTE_MAX_EDIT_RATIO = float(_get("analytes.max_edit_ratio", _DEFAULTS["analytes"]["max_edit_ratio"]))
ANALYTE_RATIO_FLOOR = float(_get("analytes.ratio_floor", _DEFAULTS["analytes"]["ratio_floor"]))

# ── База данных ───────────────────────────────────────────────────────────────
SQLITE_PATH = str(_resolve_path(os.getenv("SQLITE_PATH", _get("database.sqlite_path", _DEFAULTS["database"]["sqlite_path"]))))

# ── Telegram бот ──────────────────────────────────────────────────────────────
BOT_POLLING_TIMEOUT = int(_get("bot.polling_timeout", _DEFAULTS["bot"]["polling_timeout"]))
BOT_API_URL = os.getenv("API_URL", _get("bot.api_url", _DEFAULTS["bot"]["api_url"]))
# Потолок поллинга прогресса документа в боте. Увязан с потолком обработки на бэкенде:
# classify + общий extract + добор страниц, каждый VLM-вызов ограничен VLM_REQUEST_TIMEOUT.
# Иначе бот сдаётся раньше, чем бэкенд закончит (см. инцидент с D3).
BOT_PROGRESS_TIMEOUT = float(os.getenv("BOT_PROGRESS_TIMEOUT", str(30 + 3 * VLM_REQUEST_TIMEOUT)))

# ── Загрузка файлов ───────────────────────────────────────────────────────────
UPLOAD_MAX_BYTES = int(_get("upload.max_bytes", _DEFAULTS["upload"]["max_bytes"]))
UPLOAD_ALLOWED_EXTENSIONS: set[str] = set(_get("upload.allowed_extensions", _DEFAULTS["upload"]["allowed_extensions"]))
UPLOAD_SOURCES_DIR = _resolve_path(os.getenv("SOURCES_DIR", _get("upload.sources_dir", _DEFAULTS["upload"]["sources_dir"])))

# Задержка перед push-fallback доставки финала (> таймаута поллинга бота 120с).
DELIVERY_FALLBACK_DELAY = float(os.getenv("DELIVERY_FALLBACK_DELAY", "130"))