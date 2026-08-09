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

from pydantic import BaseModel

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
        # Компактный построчный вывод вместо JSON-схемы на текстовом слое: ключи JSON
        # стоят больше токенов, чем данные (замер: вызов быстрее в 1.9–2.4 раза).
        # При пустом разборе код сам откатывается на JSON-схему.
        "compact_output": True,
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
        "extract_long_side": 1280,
        "jpeg_quality": 90,
        "classify_long_side": 768,
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
    "storage": {
        # local — файлы на диске (по умолчанию); minio — S3-хранилище с версиями.
        "backend": "local",
        "minio": {
            "endpoint": "localhost:9000",
            "bucket": "botkin-documents",
            "secure": False,
        },
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
        # Мощная uncensored-модель для комплексных рекомендаций по образу жизни:
        # медицинские темы без отказов (лучшая по бенчу habr/bench-health-report).
        "lifestyle_model": "huihui_ai/Qwen3.6-abliterated:27b",
        # Комплексный разбор длиннее Q&A-ответа: 2048 обрезал «Взаимодействия» на живом
        # прогоне (боевая БД, 7 препаратов) — 4096 с запасом.
        "lifestyle_num_predict": 4096,
        # Живой веб-доступ модели: подмешивание веб-поиска и PubMed в контекст.
        "web_enabled": False,
        "web_results": 4,
        # Research-RAG: свежие публикации PubMed по темам (автономное обновление).
        "research": {
            "tool": "botkin-rag",
            "email": "botkin@example.com",
            "per_topic": 15,
            "topics": [
                "lymphocytosis differential diagnosis",
                "monocytosis causes clinical significance",
                "basophilia clinical interpretation",
                "complete blood count abnormalities interpretation",
                "cerebrospinal fluid cell count interpretation",
                "elevated lymphocytes viral infection",
            ],
        },
    },
    "auth": {
        # telegram_user_id, которым при первом входе присваивается роль admin.
        # Демо-уровень: идентификация остаётся по заголовку, ролью управляет БД.
        "admin_telegram_ids": [],
    },
    "health": {
        "tokens_dir": "./data/health_tokens",
        "sync_days": 30,
        "request_pause": 0.5,
    },
    "external": {
        # Координаты по умолчанию для погоды (Москва).
        # Пользователь может переопределить через профиль.
        "default_latitude": 55.7558,
        "default_longitude": 37.6173,
        "weather_enabled": True,
        "geomagnetic_enabled": True,
        "astrology_enabled": False,
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


def _default_for(key_path: str) -> object:
    """Значение из _DEFAULTS по точечному пути; None, если пути там нет."""
    value = _DEFAULTS
    for part in key_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _get(key_path: str, default: object = None) -> object:
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


def _as_bool(v: object) -> bool:
    """Истинность строки/значения env: 1/true/yes/on (регистронезависимо)."""
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def setting(key_path: str, env_name: str, cast: Callable[[object], object] = str) -> object:
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


# ---------------------------------------------------------------------------
# Типизированные модели конфигурации (pydantic).
# Валидируют значения при загрузке, дают IDE-автодополнение и единый источник правды.
# Заполняются из тех же setting()/_get() функций — приоритет env → config.json →
# _DEFAULTS сохраняется. Модульные константы ниже экспортируют значения из Settings.
# ---------------------------------------------------------------------------

class VlmConfig(BaseModel):
    model: str
    temperature: float
    classify_temperature: float
    num_ctx: int
    max_tokens: int
    num_predict: int
    repeat_penalty: float
    structured_output: bool
    max_retries: int
    retry_max_seconds: float
    retry_initial_wait: float
    retry_max_wait: float
    request_timeout: float


class TextModelConfig(BaseModel):
    model: str
    temperature: float
    num_ctx: int
    max_tokens: int
    num_predict: int
    repeat_penalty: float
    structured_output: bool
    compact_output: bool


class TextLayerConfig(BaseModel):
    min_chars_per_page: int
    y_tolerance: float
    verbatim_max_reject_ratio: float
    temperature: float
    raw_log_limit: int


class OllamaConfig(BaseModel):
    url: str
    keep_alive: str
    probe_timeout: float
    wsl_detect_timeout: float
    warmup_timeout: float


class PdfToImageConfig(BaseModel):
    render_dpi: int
    max_pages: int


class ImageConfig(BaseModel):
    extract_long_side: int
    jpeg_quality: int
    classify_long_side: int
    clahe_clip: float
    clahe_tile: int
    unsharp_amount: float
    unsharp_sigma: float
    deskew_min_angle: float
    deskew_min_area: float
    deskew_max_area: float
    deskew_open_kernel: int
    deskew_close_kernel: int
    lowres_warn: int


class DatabaseConfig(BaseModel):
    sqlite_path: str


class BotConfig(BaseModel):
    api_url: str
    progress_timeout: float
    web_debug_user_id: int
    admin_telegram_ids: frozenset[int]
    delivery_fallback_delay: float


class StorageConfig(BaseModel):
    backend: str
    minio_endpoint: str
    minio_bucket: str
    minio_secure: bool
    minio_access_key: str
    minio_secret_key: str


class UploadConfig(BaseModel):
    max_bytes: int
    allowed_extensions: set[str]
    sources_dir: Path


class DrugsConfig(BaseModel):
    max_edit_ratio: float
    ratio_floor: float


class AnalytesConfig(BaseModel):
    max_edit_ratio: float
    ratio_floor: float
    short_key_len: int


class RagConfig(BaseModel):
    embed_model: str
    embed_batch: int
    top_k: int
    recommend_model: str
    recommend_num_ctx: int
    recommend_num_predict: int
    lifestyle_model: str
    lifestyle_num_predict: int
    web_enabled: bool
    web_results: int
    research_tool: str
    research_email: str
    research_per_topic: int
    research_topics: list[str]


class HealthConfig(BaseModel):
    tokens_dir: Path
    sync_days: int
    request_pause: float
    strava_client_id: str
    strava_client_secret: str


class ExternalConfig(BaseModel):
    default_latitude: float
    default_longitude: float
    weather_enabled: bool
    geomagnetic_enabled: bool
    astrology_enabled: bool


class Settings(BaseModel):
    vlm: VlmConfig
    text_model: TextModelConfig
    text_layer: TextLayerConfig
    ollama: OllamaConfig
    pdf_to_image: PdfToImageConfig
    image: ImageConfig
    database: DatabaseConfig
    bot: BotConfig
    storage: StorageConfig
    upload: UploadConfig
    drugs: DrugsConfig
    analytes: AnalytesConfig
    rag: RagConfig
    health: HealthConfig
    external: ExternalConfig


def _build_settings() -> Settings:
    """Собирает Settings из env → config.json → _DEFAULTS (через setting()/_get())."""
    _vlm_request_timeout = float(os.getenv("VLM_REQUEST_TIMEOUT", "120"))
    _admin_ids_env = os.getenv("ADMIN_TELEGRAM_IDS", "")
    _admin_ids: frozenset[int] = frozenset(
        int(x) for x in _admin_ids_env.split(",") if x.strip().isdigit()
    ) or frozenset(int(x) for x in _get("auth.admin_telegram_ids"))
    return Settings(
        vlm=VlmConfig(
            model=setting("vlm.model", "VLM_MODEL", str),
            temperature=setting("vlm.temperature", "VLM_TEMPERATURE", float),
            classify_temperature=setting("vlm.classify_temperature", "CLASSIFY_TEMPERATURE", float),
            num_ctx=setting("vlm.num_ctx", "VLM_NUM_CTX", int),
            max_tokens=setting("vlm.max_tokens", "VLM_MAX_TOKENS", int),
            num_predict=setting("vlm.num_predict", "VLM_NUM_PREDICT", int),
            repeat_penalty=setting("vlm.repeat_penalty", "VLM_REPEAT_PENALTY", float),
            structured_output=setting("vlm.structured_output", "VLM_STRUCTURED_OUTPUT", _as_bool),
            max_retries=int(os.getenv("VLM_MAX_RETRIES", "2")),
            retry_max_seconds=float(os.getenv("VLM_RETRY_MAX_SECONDS", "300")),
            retry_initial_wait=float(os.getenv("VLM_RETRY_INITIAL_WAIT", "1.0")),
            retry_max_wait=float(os.getenv("VLM_RETRY_MAX_WAIT", "10.0")),
            request_timeout=_vlm_request_timeout,
        ),
        text_model=TextModelConfig(
            model=setting("text_model.model", "TEXT_MODEL", str),
            temperature=setting("text_model.temperature", "TEXT_TEMPERATURE", float),
            num_ctx=setting("text_model.num_ctx", "TEXT_NUM_CTX", int),
            max_tokens=setting("text_model.max_tokens", "TEXT_MAX_TOKENS", int),
            num_predict=setting("text_model.num_predict", "TEXT_NUM_PREDICT", int),
            repeat_penalty=setting("text_model.repeat_penalty", "TEXT_REPEAT_PENALTY", float),
            structured_output=setting("text_model.structured_output", "TEXT_STRUCTURED_OUTPUT", _as_bool),
            compact_output=setting("text_model.compact_output", "TEXT_COMPACT_OUTPUT", _as_bool),
        ),
        text_layer=TextLayerConfig(
            min_chars_per_page=int(os.getenv("TEXT_LAYER_MIN_CHARS_PER_PAGE", "50")),
            y_tolerance=float(os.getenv("TEXT_LAYER_Y_TOLERANCE", "3.0")),
            verbatim_max_reject_ratio=float(os.getenv("VERBATIM_MAX_REJECT_RATIO", "0.5")),
            temperature=float(os.getenv("TEXT_LAYER_TEMPERATURE", "0.0")),
            raw_log_limit=int(os.getenv("RAW_LOG_LIMIT", "4000")),
        ),
        ollama=OllamaConfig(
            url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            keep_alive=setting("ollama.keep_alive", "OLLAMA_KEEP_ALIVE", str),
            probe_timeout=setting("ollama.probe_timeout", "OLLAMA_PROBE_TIMEOUT", float),
            wsl_detect_timeout=setting("ollama.wsl_detect_timeout", "OLLAMA_WSL_DETECT_TIMEOUT", float),
            warmup_timeout=setting("ollama.warmup_timeout", "OLLAMA_WARMUP_TIMEOUT", float),
        ),
        pdf_to_image=PdfToImageConfig(
            render_dpi=setting("pdf_to_image.render_dpi", "PDF_RENDER_DPI", int),
            max_pages=setting("pdf_to_image.max_pages", "MAX_PAGES", int),
        ),
        image=ImageConfig(
            extract_long_side=setting("image.extract_long_side", "IMAGE_EXTRACT_LONG_SIDE", int),
            jpeg_quality=setting("image.jpeg_quality", "IMAGE_JPEG_QUALITY", int),
            classify_long_side=setting("image.classify_long_side", "IMAGE_CLASSIFY_LONG_SIDE", int),
            clahe_clip=setting("image.clahe_clip", "IMAGE_CLAHE_CLIP", float),
            clahe_tile=setting("image.clahe_tile", "IMAGE_CLAHE_TILE", int),
            unsharp_amount=setting("image.unsharp_amount", "IMAGE_UNSHARP_AMOUNT", float),
            unsharp_sigma=setting("image.unsharp_sigma", "IMAGE_UNSHARP_SIGMA", float),
            deskew_min_angle=setting("image.deskew_min_angle", "IMAGE_DESKEW_MIN_ANGLE", float),
            deskew_min_area=setting("image.deskew_min_area", "IMAGE_DESKEW_MIN_AREA", float),
            deskew_max_area=setting("image.deskew_max_area", "IMAGE_DESKEW_MAX_AREA", float),
            deskew_open_kernel=setting("image.deskew_open_kernel", "IMAGE_DESKEW_OPEN_KERNEL", int),
            deskew_close_kernel=setting("image.deskew_close_kernel", "IMAGE_DESKEW_CLOSE_KERNEL", int),
            lowres_warn=setting("image.lowres_warn", "PHOTO_LOWRES_WARN", int),
        ),
        database=DatabaseConfig(
            sqlite_path=str(_resolve_path(os.getenv("SQLITE_PATH", _get("database.sqlite_path")))),
        ),
        bot=BotConfig(
            api_url=setting("bot.api_url", "API_URL", str),
            progress_timeout=float(os.getenv("BOT_PROGRESS_TIMEOUT", str(30 + 3 * _vlm_request_timeout))),
            web_debug_user_id=int(os.getenv("WEB_DEBUG_USER_ID", "0") or 0),
            admin_telegram_ids=_admin_ids,
            delivery_fallback_delay=float(os.getenv("DELIVERY_FALLBACK_DELAY", "130")),
        ),
        storage=StorageConfig(
            backend=setting("storage.backend", "STORAGE_BACKEND", str),
            minio_endpoint=setting("storage.minio.endpoint", "MINIO_ENDPOINT", str),
            minio_bucket=setting("storage.minio.bucket", "MINIO_BUCKET", str),
            minio_secure=_as_bool(setting("storage.minio.secure", "MINIO_SECURE", str)),
            minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            minio_secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        ),
        upload=UploadConfig(
            max_bytes=setting("upload.max_bytes", "UPLOAD_MAX_BYTES", int),
            allowed_extensions=set(_get("upload.allowed_extensions")),
            sources_dir=_resolve_path(os.getenv("SOURCES_DIR", _get("upload.sources_dir"))),
        ),
        drugs=DrugsConfig(
            max_edit_ratio=setting("drugs.max_edit_ratio", "DRUG_MAX_EDIT_RATIO", float),
            ratio_floor=setting("drugs.ratio_floor", "DRUG_RATIO_FLOOR", float),
        ),
        analytes=AnalytesConfig(
            max_edit_ratio=setting("analytes.max_edit_ratio", "ANALYTE_MAX_EDIT_RATIO", float),
            ratio_floor=setting("analytes.ratio_floor", "ANALYTE_RATIO_FLOOR", float),
            short_key_len=setting("analytes.short_key_len", "ANALYTE_SHORT_KEY_LEN", int),
        ),
        rag=RagConfig(
            embed_model=setting("rag.embed_model", "RAG_EMBED_MODEL", str),
            embed_batch=setting("rag.embed_batch", "RAG_EMBED_BATCH", int),
            top_k=setting("rag.top_k", "RAG_TOP_K", int),
            recommend_model=setting("rag.recommend_model", "RAG_RECOMMEND_MODEL", str),
            recommend_num_ctx=setting("rag.recommend_num_ctx", "RAG_RECOMMEND_NUM_CTX", int),
            recommend_num_predict=setting("rag.recommend_num_predict", "RAG_RECOMMEND_NUM_PREDICT", int),
            lifestyle_model=setting("rag.lifestyle_model", "RAG_LIFESTYLE_MODEL", str),
            lifestyle_num_predict=setting("rag.lifestyle_num_predict", "RAG_LIFESTYLE_NUM_PREDICT", int),
            web_enabled=setting("rag.web_enabled", "RAG_WEB_ENABLED", _as_bool),
            web_results=setting("rag.web_results", "RAG_WEB_RESULTS", int),
            research_tool=_get("rag.research.tool"),
            research_email=_get("rag.research.email"),
            research_per_topic=int(_get("rag.research.per_topic")),
            research_topics=list(_get("rag.research.topics")),
        ),
        health=HealthConfig(
            tokens_dir=_resolve_path(os.getenv("HEALTH_TOKENS_DIR", _get("health.tokens_dir"))),
            sync_days=setting("health.sync_days", "HEALTH_SYNC_DAYS", int),
            request_pause=setting("health.request_pause", "HEALTH_REQUEST_PAUSE", float),
            strava_client_id=os.getenv("STRAVA_CLIENT_ID", ""),
            strava_client_secret=os.getenv("STRAVA_CLIENT_SECRET", ""),
        ),
        external=ExternalConfig(
            default_latitude=setting("external.default_latitude", "EXT_DEFAULT_LAT", float),
            default_longitude=setting("external.default_longitude", "EXT_DEFAULT_LON", float),
            weather_enabled=setting("external.weather_enabled", "EXT_WEATHER_ENABLED", _as_bool),
            geomagnetic_enabled=setting("external.geomagnetic_enabled", "EXT_GEOMAGNETIC_ENABLED", _as_bool),
            astrology_enabled=setting("external.astrology_enabled", "EXT_ASTROLOGY_ENABLED", _as_bool),
        ),
    )


settings = _build_settings()


# VLM
VLM_MODEL = settings.vlm.model
VLM_TEMPERATURE = settings.vlm.temperature
# Классификация — задача «один из N», не OCR: небольшой шум (0.1) помогает модели
# не застревать в ошибочном варианте на пограничных бланках. Extract — наоборот, детерминизм.
CLASSIFY_TEMPERATURE = settings.vlm.classify_temperature
VLM_NUM_CTX = settings.vlm.num_ctx
VLM_MAX_TOKENS = settings.vlm.max_tokens
VLM_NUM_PREDICT = settings.vlm.num_predict
VLM_REPEAT_PENALTY = settings.vlm.repeat_penalty
# Сколько раз instructor переспросит модель при невалидном по схеме ответе.
VLM_MAX_RETRIES = settings.vlm.max_retries
# Ретраи VLM-вызовов (tenacity): экспоненциальный backoff с джиттером, стоп по числу
# попыток (VLM_MAX_RETRIES) И по суммарному времени. Деградировавшая модель не должна
# крутить минутами; джиттер разводит одновременные ретраи.
VLM_RETRY_MAX_SECONDS = settings.vlm.retry_max_seconds
VLM_RETRY_INITIAL_WAIT = settings.vlm.retry_initial_wait
VLM_RETRY_MAX_WAIT = settings.vlm.retry_max_wait
# Потолок одного VLM-вызова. Деградировавший вызов (генерация дублей) не должен висеть
# минутами — по таймауту прерываем, страница пропускается, документ сохраняет остальное.
VLM_REQUEST_TIMEOUT = settings.vlm.request_timeout
# Принуждение JSON-схемы на уровне декодера Ollama (нативный параметр format → XGrammar,
# 100% соответствие). При выключении — откат на prompt-only JSON (instructor Mode.JSON).
# Флаг — страховка: конкретная версия Ollama может повести себя иначе на /v1 (см. #10001).
VLM_STRUCTURED_OUTPUT = settings.vlm.structured_output

# OCR-специализированная модель первой ступени двухступенчатого пайплайна.
# Если не задана — используется тот же VLM, чтобы старый одноступенчатый путь
# продолжал работать без доработки конфига.
OCR_MODEL = os.getenv("OCR_MODEL") or VLM_MODEL
OCR_TEMPERATURE = float(os.getenv("OCR_TEMPERATURE", str(VLM_TEMPERATURE)))
OCR_NUM_CTX = int(os.getenv("OCR_NUM_CTX", str(VLM_NUM_CTX)))
OCR_MAX_TOKENS = int(os.getenv("OCR_MAX_TOKENS", str(VLM_MAX_TOKENS)))
OCR_NUM_PREDICT = int(os.getenv("OCR_NUM_PREDICT", str(VLM_NUM_PREDICT)))
OCR_REPEAT_PENALTY = float(os.getenv("OCR_REPEAT_PENALTY", str(VLM_REPEAT_PENALTY)))

# Text-only модель для обработки текстового слоя PDF.
# По умолчанию равна VLM, но может быть лёгкой text-only моделью (qwen3:1.7b, qwen3:8b).
TEXT_MODEL = settings.text_model.model
TEXT_TEMPERATURE = settings.text_model.temperature
TEXT_NUM_CTX = settings.text_model.num_ctx
TEXT_MAX_TOKENS = settings.text_model.max_tokens
TEXT_NUM_PREDICT = settings.text_model.num_predict
TEXT_REPEAT_PENALTY = settings.text_model.repeat_penalty
TEXT_STRUCTURED_OUTPUT = settings.text_model.structured_output
TEXT_COMPACT_OUTPUT = settings.text_model.compact_output

# Текстовый слой PDF (детерминированное извлечение без VLM).
# Минимум символов на страницу, чтобы считать слой годным (отсекает PDF-сканы
# с пустым/мусорным текстовым слоем).
TEXT_LAYER_MIN_CHARS_PER_PAGE = settings.text_layer.min_chars_per_page
# Толеранция по Y (в пунктах) при кластеризации слов в физические строки:
# значение часто сидит на 1px ниже имени, наивное округление разрывает строку.
TEXT_LAYER_Y_TOLERANCE = settings.text_layer.y_tolerance
# Доля забракованных verbatim-стражем чисел, выше которой результат считается
# недостоверным → фолбэк на VLM.
VERBATIM_MAX_REJECT_RATIO = settings.text_layer.verbatim_max_reject_ratio
# Детерминированный (temp=0) text-only вызов структурирования слоя — temperature всегда 0,
# текст уже точный, креатив модели только навредит.
TEXT_LAYER_TEMPERATURE = settings.text_layer.temperature
# Потолок длины сырого ответа модели в DEBUG-логе (символов) — чтобы не залить лог мегабайтами.
RAW_LOG_LIMIT = settings.text_layer.raw_log_limit

# Ollama
OLLAMA_URL = settings.ollama.url
# keep_alive держит модель в VRAM между вызовами — нет перезагрузки весов 6 ГБ
OLLAMA_KEEP_ALIVE = settings.ollama.keep_alive
# Таймаут пробы доступности Ollama (GET /api/version) — короткий, это лишь ping.
OLLAMA_PROBE_TIMEOUT = settings.ollama.probe_timeout
# Таймаут определения IP WSL (wsl hostname -I) на Windows.
OLLAMA_WSL_DETECT_TIMEOUT = settings.ollama.wsl_detect_timeout
# Таймаут прогрева (загрузки весов в VRAM): холодный старт 6 ГБ модели ~100–120s,
# берём с запасом. Прогрев best-effort — превышение таймаута не роняет сервис.
OLLAMA_WARMUP_TIMEOUT = settings.ollama.warmup_timeout

# PDF → изображение
PDF_RENDER_DPI = settings.pdf_to_image.render_dpi
MAX_PAGES = settings.pdf_to_image.max_pages

# Подготовка изображений
IMAGE_EXTRACT_LONG_SIDE = settings.image.extract_long_side
IMAGE_JPEG_QUALITY = settings.image.jpeg_quality
IMAGE_CLASSIFY_LONG_SIDE = settings.image.classify_long_side
IMAGE_CLAHE_CLIP = settings.image.clahe_clip
# Размер сетки CLAHE (tileGridSize = N×N) — локальность адаптивного контраста.
IMAGE_CLAHE_TILE = settings.image.clahe_tile
IMAGE_UNSHARP_AMOUNT = settings.image.unsharp_amount
# Сигма гауссова размытия для unsharp-маски (радиус мягкости).
IMAGE_UNSHARP_SIGMA = settings.image.unsharp_sigma
IMAGE_DESKEW_MIN_ANGLE = settings.image.deskew_min_angle
IMAGE_DESKEW_MIN_AREA = settings.image.deskew_min_area
IMAGE_DESKEW_MAX_AREA = settings.image.deskew_max_area
# Морфо-ядра поиска листа при deskew: OPEN убирает шум, CLOSE сшивает лист (N×N).
IMAGE_DESKEW_OPEN_KERNEL = settings.image.deskew_open_kernel
IMAGE_DESKEW_CLOSE_KERNEL = settings.image.deskew_close_kernel
PHOTO_LOWRES_WARN = settings.image.lowres_warn

# Нормализация лекарств.
# Scorer = дистанция Дамерау-Левенштейна (выбран по замеру на словаре 20 948, см. спек):
# cap = max(1, floor(len(имя) * DRUG_MAX_EDIT_RATIO)); фильтр fuzz.ratio ≥ DRUG_RATIO_FLOOR.
DRUG_MAX_EDIT_RATIO = settings.drugs.max_edit_ratio
DRUG_RATIO_FLOOR = settings.drugs.ratio_floor

# Нормализация анализов (ФСЛИ).
# Аналогично препаратам: cap по дистанции Дамерау-Левенштейна + ratio-floor.
ANALYTE_MAX_EDIT_RATIO = settings.analytes.max_edit_ratio
ANALYTE_RATIO_FLOOR = settings.analytes.ratio_floor
# Длина ключа (символов), при которой требуется точное совпадение: короткие аббревиатуры
# (СОЭ, ЦП) нельзя фаззить — одна правка превращает их в чужой показатель.
ANALYTE_SHORT_KEY_LEN = settings.analytes.short_key_len

# База данных
SQLITE_PATH = settings.database.sqlite_path

# Telegram бот
# Историческое имя env — API_URL (не BOT_API_URL), сохраняем для совместимости.
BOT_API_URL = settings.bot.api_url
# Потолок поллинга прогресса документа в боте. Увязан с потолком обработки на бэкенде:
# classify + общий extract + добор страниц, каждый VLM-вызов ограничен VLM_REQUEST_TIMEOUT.
# Иначе бот сдаётся раньше, чем бэкенд закончит (см. инцидент с D3).
BOT_PROGRESS_TIMEOUT = settings.bot.progress_timeout

# Веб-кабинет: дебаг-вход без заголовка X-Telegram-User-Id.
# Если задан (> 0) и заголовок отсутствует — API работает от имени этого
# telegram_user_id. ТОЛЬКО для локального запуска/дебага; в проде не задавать.
WEB_DEBUG_USER_ID = settings.bot.web_debug_user_id

# Бутстрап ролей: перечисленные telegram_user_id получают роль admin при первом
# обращении (get_or_create). Дальше ролями управляет админ через /api/admin/users.
ADMIN_TELEGRAM_IDS = settings.bot.admin_telegram_ids

# Хранилище оригиналов документов: local (диск) или minio (S3 с версионированием).
# Секреты MinIO — только из env (не хранить ключи в config.json под git).
STORAGE_BACKEND = settings.storage.backend
MINIO_ENDPOINT = settings.storage.minio_endpoint
MINIO_BUCKET = settings.storage.minio_bucket
MINIO_SECURE = settings.storage.minio_secure
MINIO_ACCESS_KEY = settings.storage.minio_access_key
MINIO_SECRET_KEY = settings.storage.minio_secret_key

# Загрузка файлов
UPLOAD_MAX_BYTES = settings.upload.max_bytes
UPLOAD_ALLOWED_EXTENSIONS = settings.upload.allowed_extensions
UPLOAD_SOURCES_DIR = settings.upload.sources_dir

# Задержка перед push-fallback доставки финала (> таймаута поллинга бота 120с).
DELIVERY_FALLBACK_DELAY = settings.bot.delivery_fallback_delay

# RAG: эмбеддинги (bge-m3 через Ollama /api/embed, 1024-dim) и векторный поиск.
RAG_EMBED_MODEL = settings.rag.embed_model
RAG_EMBED_BATCH = settings.rag.embed_batch
RAG_TOP_K = settings.rag.top_k
# Text-only модель для генерации рекомендаций с RAG-контекстом.
RAG_RECOMMEND_MODEL = settings.rag.recommend_model
RAG_RECOMMEND_NUM_CTX = settings.rag.recommend_num_ctx
RAG_RECOMMEND_NUM_PREDICT = settings.rag.recommend_num_predict
# Мощная uncensored-модель для комплексных lifestyle-рекомендаций.
RAG_LIFESTYLE_MODEL = settings.rag.lifestyle_model
RAG_LIFESTYLE_NUM_PREDICT = settings.rag.lifestyle_num_predict

# Живой веб-доступ модели: веб-поиск + PubMed в контекст рекомендации.
RAG_WEB_ENABLED = settings.rag.web_enabled
RAG_WEB_RESULTS = settings.rag.web_results
# Research-RAG: свежие публикации PubMed.
RESEARCH_TOOL = settings.rag.research_tool
RESEARCH_EMAIL = settings.rag.research_email
RESEARCH_PER_TOPIC = settings.rag.research_per_topic
RESEARCH_TOPICS = settings.rag.research_topics

# Health-sync: каталог OAuth-токенов (вне git), глубина первичной синхронизации (дней)
# и пауза между запросами к провайдеру (бережём rate limit Garmin).
HEALTH_TOKENS_DIR = settings.health.tokens_dir
HEALTH_SYNC_DAYS = settings.health.sync_days
HEALTH_REQUEST_PAUSE = settings.health.request_pause
# Strava OAuth (опционально): без client_id/secret подключение Strava отключено.
STRAVA_CLIENT_ID = settings.health.strava_client_id
STRAVA_CLIENT_SECRET = settings.health.strava_client_secret

# Внешние данные для рекомендаций: погода (Open-Meteo), геомагнитная активность
# (NOAA SWPC), астрология (развлекательный модуль, по умолчанию выключен).
EXT_DEFAULT_LAT = settings.external.default_latitude
EXT_DEFAULT_LON = settings.external.default_longitude
EXT_WEATHER_ENABLED = settings.external.weather_enabled
EXT_GEOMAGNETIC_ENABLED = settings.external.geomagnetic_enabled
EXT_ASTROLOGY_ENABLED = settings.external.astrology_enabled