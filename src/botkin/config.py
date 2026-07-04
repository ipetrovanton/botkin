"""Единая точка конфигурации приложения.

Приоритет источников:
1. Переменные окружения (из .env через python-dotenv)
2. config.json в корне проекта
3. Жёстко заданные значения по умолчанию
"""
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
        "classify_temperature": 0.1,
        "num_ctx": 16384,
        "max_tokens": 8192,
        "num_predict": 8192,
        "repeat_penalty": 1.2,
        "structured_output": True,
    },
    # Текстовая модель для обработки текстового слоя PDF (run_analysis text_layer).
    # По умолчанию совпадает с VLM, но для скорости лучше выбрать лёгкую text-only
    # модель, например qwen3:1.7b (1.4 GB) или qwen3:8b (≈5 GB).
    "text_model": {
        "model": "qwen3-vl:8b-instruct",
        "temperature": 0.0,
        "num_ctx": 4096,
        "max_tokens": 8192,
        "num_predict": 8192,
        "repeat_penalty": 1.2,
        "structured_output": True,
    },
    "ollama": {
        "keep_alive": "30m",
        "probe_timeout": 1.5,
        "wsl_detect_timeout": 5.0,
        "warmup_timeout": 300.0,
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
        "clahe_tile": 8,
        "unsharp_amount": 1.5,
        "unsharp_sigma": 3.0,
        "deskew_min_angle": 3.0,
        "deskew_min_area": 0.40,
        "deskew_max_area": 0.97,
        "deskew_open_kernel": 9,
        "deskew_close_kernel": 35,
        "lowres_warn": 1500,
    },
    "database": {
        "sqlite_path": "./data/botkin.db",
    },
    "bot": {
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
        "short_key_len": 3,
    },
    "rag": {
        "embed_model": "bge-m3",
        "embed_batch": 64,
        "top_k": 8,
        "recommend_model": "qwen3:8b",
        "recommend_num_ctx": 8192,
        "recommend_num_predict": 2048,
    },
    "health": {
        "tokens_dir": "./data/health_tokens",
        "sync_days": 30,
        "request_pause": 0.5,
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


def _default_for(key_path: str):
    """Значение из _DEFAULTS по точечному пути; None, если пути там нет."""
    value = _DEFAULTS
    for part in key_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _get(key_path: str):
    """config.json по пути, иначе дефолт из _DEFAULTS. Дефолт хранится в одном месте."""
    value = _json
    for part in key_path.split("."):
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(part)
        if value is None:
            break
    return value if value is not None else _default_for(key_path)


def _as_bool(v) -> bool:
    """Истинность строки/значения env: 1/true/yes/on (регистронезависимо)."""
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def setting(key_path: str, env_name: str, cast: Callable[[Any], Any] = str) -> Any:
    """Единый порядок разрешения настройки: env → config.json → _DEFAULTS.

    Приведение типа cast применяется в одной точке к любому источнику — закрывает
    12-factor дыру, где часть констант (VLM_*) читала env, а IMAGE_*/DRUG_*/ANALYTE_*/
    PDF_RENDER_DPI/MAX_PAGES — нет. Только для скаляров; списки/множества/пути — отдельно.
    """
    raw = os.getenv(env_name)
    if raw is not None:
        return cast(raw)
    return cast(_get(key_path))


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (_project_root / p)


# VLM
VLM_MODEL = setting("vlm.model", "VLM_MODEL", str)
VLM_TEMPERATURE = setting("vlm.temperature", "VLM_TEMPERATURE", float)
# Классификация — задача «один из N», не OCR: небольшой шум (0.1) помогает модели
# не застревать в ошибочном варианте на пограничных бланках. Extract — наоборот, детерминизм.
CLASSIFY_TEMPERATURE = setting("vlm.classify_temperature", "CLASSIFY_TEMPERATURE", float)
VLM_NUM_CTX = setting("vlm.num_ctx", "VLM_NUM_CTX", int)
VLM_MAX_TOKENS = setting("vlm.max_tokens", "VLM_MAX_TOKENS", int)
VLM_NUM_PREDICT = setting("vlm.num_predict", "VLM_NUM_PREDICT", int)
VLM_REPEAT_PENALTY = setting("vlm.repeat_penalty", "VLM_REPEAT_PENALTY", float)
# Сколько раз instructor переспросит модель при невалидном по схеме ответе.
VLM_MAX_RETRIES = int(os.getenv("VLM_MAX_RETRIES", "2"))
# Ретраи VLM-вызовов (tenacity): экспоненциальный backoff с джиттером, стоп по числу
# попыток (VLM_MAX_RETRIES) И по суммарному времени. Деградировавшая модель не должна
# крутить минутами; джиттер разводит одновременные ретраи.
VLM_RETRY_MAX_SECONDS = float(os.getenv("VLM_RETRY_MAX_SECONDS", "300"))
VLM_RETRY_INITIAL_WAIT = float(os.getenv("VLM_RETRY_INITIAL_WAIT", "1.0"))
VLM_RETRY_MAX_WAIT = float(os.getenv("VLM_RETRY_MAX_WAIT", "10.0"))
# Потолок одного VLM-вызова. Деградировавший вызов (генерация дублей) не должен висеть
# минутами — по таймауту прерываем, страница пропускается, документ сохраняет остальное.
VLM_REQUEST_TIMEOUT = float(os.getenv("VLM_REQUEST_TIMEOUT", "120"))
# Принуждение JSON-схемы на уровне декодера Ollama (нативный параметр format → XGrammar,
# 100% соответствие). При выключении — откат на prompt-only JSON (instructor Mode.JSON).
# Флаг — страховка: конкретная версия Ollama может повести себя иначе на /v1 (см. #10001).
VLM_STRUCTURED_OUTPUT = setting("vlm.structured_output", "VLM_STRUCTURED_OUTPUT", _as_bool)

# Text-only модель для обработки текстового слоя PDF.
# По умолчанию равна VLM, но может быть лёгкой text-only моделью (qwen3:1.7b, qwen3:8b).
TEXT_MODEL = setting("text_model.model", "TEXT_MODEL", str)
TEXT_TEMPERATURE = setting("text_model.temperature", "TEXT_TEMPERATURE", float)
TEXT_NUM_CTX = setting("text_model.num_ctx", "TEXT_NUM_CTX", int)
TEXT_MAX_TOKENS = setting("text_model.max_tokens", "TEXT_MAX_TOKENS", int)
TEXT_NUM_PREDICT = setting("text_model.num_predict", "TEXT_NUM_PREDICT", int)
TEXT_REPEAT_PENALTY = setting("text_model.repeat_penalty", "TEXT_REPEAT_PENALTY", float)
TEXT_STRUCTURED_OUTPUT = setting("text_model.structured_output", "TEXT_STRUCTURED_OUTPUT", _as_bool)

# Текстовый слой PDF (детерминированное извлечение без VLM).
# Минимум символов на страницу, чтобы считать слой годным (отсекает PDF-сканы
# с пустым/мусорным текстовым слоем).
TEXT_LAYER_MIN_CHARS_PER_PAGE = int(os.getenv("TEXT_LAYER_MIN_CHARS_PER_PAGE", "50"))
# Толеранция по Y (в пунктах) при кластеризации слов в физические строки:
# значение часто сидит на 1px ниже имени, наивное округление разрывает строку.
TEXT_LAYER_Y_TOLERANCE = float(os.getenv("TEXT_LAYER_Y_TOLERANCE", "3.0"))
# Доля забракованных verbatim-стражем чисел, выше которой результат считается
# недостоверным → фолбэк на VLM.
VERBATIM_MAX_REJECT_RATIO = float(os.getenv("VERBATIM_MAX_REJECT_RATIO", "0.5"))
# Детерминированный (temp=0) text-only вызов структурирования слоя — temperature всегда 0,
# текст уже точный, креатив модели только навредит.
TEXT_LAYER_TEMPERATURE = float(os.getenv("TEXT_LAYER_TEMPERATURE", "0.0"))
# Потолок длины сырого ответа модели в DEBUG-логе (символов) — чтобы не залить лог мегабайтами.
RAW_LOG_LIMIT = int(os.getenv("RAW_LOG_LIMIT", "4000"))

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# keep_alive держит модель в VRAM между вызовами — нет перезагрузки весов 6 ГБ
OLLAMA_KEEP_ALIVE = setting("ollama.keep_alive", "OLLAMA_KEEP_ALIVE", str)
# Таймаут пробы доступности Ollama (GET /api/version) — короткий, это лишь ping.
OLLAMA_PROBE_TIMEOUT = setting("ollama.probe_timeout", "OLLAMA_PROBE_TIMEOUT", float)
# Таймаут определения IP WSL (wsl hostname -I) на Windows.
OLLAMA_WSL_DETECT_TIMEOUT = setting("ollama.wsl_detect_timeout", "OLLAMA_WSL_DETECT_TIMEOUT", float)
# Таймаут прогрева (загрузки весов в VRAM): холодный старт 6 ГБ модели ~100–120s,
# берём с запасом. Прогрев best-effort — превышение таймаута не роняет сервис.
OLLAMA_WARMUP_TIMEOUT = setting("ollama.warmup_timeout", "OLLAMA_WARMUP_TIMEOUT", float)

# PDF → изображение
PDF_RENDER_DPI = setting("pdf_to_image.render_dpi", "PDF_RENDER_DPI", int)
MAX_PAGES = setting("pdf_to_image.max_pages", "MAX_PAGES", int)

# Подготовка изображений
IMAGE_EXTRACT_LONG_SIDE = setting("image.extract_long_side", "IMAGE_EXTRACT_LONG_SIDE", int)
IMAGE_JPEG_QUALITY = setting("image.jpeg_quality", "IMAGE_JPEG_QUALITY", int)
IMAGE_CLASSIFY_LONG_SIDE = setting("image.classify_long_side", "IMAGE_CLASSIFY_LONG_SIDE", int)
IMAGE_CLAHE_CLIP = setting("image.clahe_clip", "IMAGE_CLAHE_CLIP", float)
# Размер сетки CLAHE (tileGridSize = N×N) — локальность адаптивного контраста.
IMAGE_CLAHE_TILE = setting("image.clahe_tile", "IMAGE_CLAHE_TILE", int)
IMAGE_UNSHARP_AMOUNT = setting("image.unsharp_amount", "IMAGE_UNSHARP_AMOUNT", float)
# Сигма гауссова размытия для unsharp-маски (радиус мягкости).
IMAGE_UNSHARP_SIGMA = setting("image.unsharp_sigma", "IMAGE_UNSHARP_SIGMA", float)
IMAGE_DESKEW_MIN_ANGLE = setting("image.deskew_min_angle", "IMAGE_DESKEW_MIN_ANGLE", float)
IMAGE_DESKEW_MIN_AREA = setting("image.deskew_min_area", "IMAGE_DESKEW_MIN_AREA", float)
IMAGE_DESKEW_MAX_AREA = setting("image.deskew_max_area", "IMAGE_DESKEW_MAX_AREA", float)
# Морфо-ядра поиска листа при deskew: OPEN убирает шум, CLOSE сшивает лист (N×N).
IMAGE_DESKEW_OPEN_KERNEL = setting("image.deskew_open_kernel", "IMAGE_DESKEW_OPEN_KERNEL", int)
IMAGE_DESKEW_CLOSE_KERNEL = setting("image.deskew_close_kernel", "IMAGE_DESKEW_CLOSE_KERNEL", int)
PHOTO_LOWRES_WARN = setting("image.lowres_warn", "PHOTO_LOWRES_WARN", int)

# Нормализация лекарств.
# Scorer = дистанция Дамерау-Левенштейна (выбран по замеру на словаре 20 948, см. спек):
# cap = max(1, floor(len(имя) * DRUG_MAX_EDIT_RATIO)); фильтр fuzz.ratio ≥ DRUG_RATIO_FLOOR.
DRUG_MAX_EDIT_RATIO = setting("drugs.max_edit_ratio", "DRUG_MAX_EDIT_RATIO", float)
DRUG_RATIO_FLOOR = setting("drugs.ratio_floor", "DRUG_RATIO_FLOOR", float)

# Нормализация анализов (ФСЛИ).
# Аналогично препаратам: cap по дистанции Дамерау-Левенштейна + ratio-floor.
ANALYTE_MAX_EDIT_RATIO = setting("analytes.max_edit_ratio", "ANALYTE_MAX_EDIT_RATIO", float)
ANALYTE_RATIO_FLOOR = setting("analytes.ratio_floor", "ANALYTE_RATIO_FLOOR", float)
# Длина ключа (символов), при которой требуется точное совпадение: короткие аббревиатуры
# (СОЭ, ЦП) нельзя фаззить — одна правка превращает их в чужой показатель.
ANALYTE_SHORT_KEY_LEN = setting("analytes.short_key_len", "ANALYTE_SHORT_KEY_LEN", int)

# База данных
SQLITE_PATH = str(_resolve_path(os.getenv("SQLITE_PATH", _get("database.sqlite_path"))))

# Telegram бот
# Историческое имя env — API_URL (не BOT_API_URL), сохраняем для совместимости.
BOT_API_URL = setting("bot.api_url", "API_URL", str)
# Потолок поллинга прогресса документа в боте. Увязан с потолком обработки на бэкенде:
# classify + общий extract + добор страниц, каждый VLM-вызов ограничен VLM_REQUEST_TIMEOUT.
# Иначе бот сдаётся раньше, чем бэкенд закончит (см. инцидент с D3).
BOT_PROGRESS_TIMEOUT = float(os.getenv("BOT_PROGRESS_TIMEOUT", str(30 + 3 * VLM_REQUEST_TIMEOUT)))

# Веб-кабинет: дебаг-вход без заголовка X-Telegram-User-Id.
# Если задан (> 0) и заголовок отсутствует — API работает от имени этого
# telegram_user_id. ТОЛЬКО для локального запуска/дебага; в проде не задавать.
WEB_DEBUG_USER_ID = int(os.getenv("WEB_DEBUG_USER_ID", "0") or 0)

# Загрузка файлов
UPLOAD_MAX_BYTES = setting("upload.max_bytes", "UPLOAD_MAX_BYTES", int)
UPLOAD_ALLOWED_EXTENSIONS: set[str] = set(_get("upload.allowed_extensions"))
UPLOAD_SOURCES_DIR = _resolve_path(os.getenv("SOURCES_DIR", _get("upload.sources_dir")))

# Задержка перед push-fallback доставки финала (> таймаута поллинга бота 120с).
DELIVERY_FALLBACK_DELAY = float(os.getenv("DELIVERY_FALLBACK_DELAY", "130"))

# RAG: эмбеддинги (bge-m3 через Ollama /api/embed, 1024-dim) и векторный поиск.
RAG_EMBED_MODEL = setting("rag.embed_model", "RAG_EMBED_MODEL", str)
RAG_EMBED_BATCH = setting("rag.embed_batch", "RAG_EMBED_BATCH", int)
RAG_TOP_K = setting("rag.top_k", "RAG_TOP_K", int)
# Text-only модель для генерации рекомендаций с RAG-контекстом.
RAG_RECOMMEND_MODEL = setting("rag.recommend_model", "RAG_RECOMMEND_MODEL", str)
RAG_RECOMMEND_NUM_CTX = setting("rag.recommend_num_ctx", "RAG_RECOMMEND_NUM_CTX", int)
RAG_RECOMMEND_NUM_PREDICT = setting("rag.recommend_num_predict", "RAG_RECOMMEND_NUM_PREDICT", int)

# Health-sync: каталог OAuth-токенов (вне git), глубина первичной синхронизации (дней)
# и пауза между запросами к провайдеру (бережём rate limit Garmin).
HEALTH_TOKENS_DIR = _resolve_path(os.getenv("HEALTH_TOKENS_DIR", _get("health.tokens_dir")))
HEALTH_SYNC_DAYS = setting("health.sync_days", "HEALTH_SYNC_DAYS", int)
HEALTH_REQUEST_PAUSE = setting("health.request_pause", "HEALTH_REQUEST_PAUSE", float)
# Strava OAuth (опционально): без client_id/secret подключение Strava отключено.
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")