"""Root settings container combining all domain settings."""

from functools import lru_cache

from botkin.settings.extraction import ExtractionSettings, NormalizeSettings
from botkin.settings.image import ImageSettings
from botkin.settings.llm import BackendSettings, OllamaSettings, TextModelSettings, VLMSettings
from botkin.settings.pdf import PDFSettings
from botkin.settings.loader import resolve, _as_bool


class AppSettings:
    """Typed application settings.

    Combines all domain-specific settings groups.
    Use get_settings() to get a cached singleton instance.
    """

    def __init__(self) -> None:
        self.vlm = VLMSettings(
            model=resolve("vlm.model", "VLM_MODEL", str, "qwen3-vl:8b-instruct"),
            temperature=resolve("vlm.temperature", "VLM_TEMPERATURE", float, 0.0),
            classify_temperature=resolve("vlm.classify_temperature", "CLASSIFY_TEMPERATURE", float, 0.1),
            num_ctx=resolve("vlm.num_ctx", "VLM_NUM_CTX", int, 16384),
            max_tokens=resolve("vlm.max_tokens", "VLM_MAX_TOKENS", int, 8192),
            num_predict=resolve("vlm.num_predict", "VLM_NUM_PREDICT", int, 8192),
            repeat_penalty=resolve("vlm.repeat_penalty", "VLM_REPEAT_PENALTY", float, 1.2),
            structured_output=resolve("vlm.structured_output", "VLM_STRUCTURED_OUTPUT", _as_bool, True),
            max_retries=int(__import__("os").getenv("VLM_MAX_RETRIES", "2")),
            retry_max_seconds=float(__import__("os").getenv("VLM_RETRY_MAX_SECONDS", "300")),
            retry_initial_wait=float(__import__("os").getenv("VLM_RETRY_INITIAL_WAIT", "1.0")),
            retry_max_wait=float(__import__("os").getenv("VLM_RETRY_MAX_WAIT", "10.0")),
            request_timeout=float(__import__("os").getenv("VLM_REQUEST_TIMEOUT", "120")),
        )
        self.text_model = TextModelSettings(
            model=resolve("text_model.model", "TEXT_MODEL", str, "qwen3-vl:8b-instruct"),
            temperature=resolve("text_model.temperature", "TEXT_TEMPERATURE", float, 0.0),
            num_ctx=resolve("text_model.num_ctx", "TEXT_NUM_CTX", int, 4096),
            max_tokens=resolve("text_model.max_tokens", "TEXT_MAX_TOKENS", int, 8192),
            num_predict=resolve("text_model.num_predict", "TEXT_NUM_PREDICT", int, 8192),
            repeat_penalty=resolve("text_model.repeat_penalty", "TEXT_REPEAT_PENALTY", float, 1.2),
            structured_output=resolve("text_model.structured_output", "TEXT_STRUCTURED_OUTPUT", _as_bool, True),
            compact_output=resolve("text_model.compact_output", "TEXT_COMPACT_OUTPUT", _as_bool, True),
        )
        self.ollama = OllamaSettings(
            url=__import__("os").getenv("OLLAMA_URL", "http://localhost:11434"),
            keep_alive=resolve("ollama.keep_alive", "OLLAMA_KEEP_ALIVE", str, "30m"),
            probe_timeout=resolve("ollama.probe_timeout", "OLLAMA_PROBE_TIMEOUT", float, 1.5),
            wsl_detect_timeout=resolve("ollama.wsl_detect_timeout", "OLLAMA_WSL_DETECT_TIMEOUT", float, 5.0),
            warmup_timeout=resolve("ollama.warmup_timeout", "OLLAMA_WARMUP_TIMEOUT", float, 300.0),
        )
        self.backend = BackendSettings(
            backend=__import__("os").getenv("LLM_BACKEND", "ollama"),
            vllm_url=__import__("os").getenv("VLLM_URL", "http://localhost:8001"),
            mlx_url=__import__("os").getenv("MLX_URL", "http://localhost:8002"),
        )
        self.image = ImageSettings(
            extract_long_side=resolve("image.extract_long_side", "IMAGE_EXTRACT_LONG_SIDE", int, 2200),
            jpeg_quality=resolve("image.jpeg_quality", "IMAGE_JPEG_QUALITY", int, 90),
            classify_long_side=resolve("image.classify_long_side", "IMAGE_CLASSIFY_LONG_SIDE", int, 1000),
            clahe_clip=resolve("image.clahe_clip", "IMAGE_CLAHE_CLIP", float, 2.0),
            clahe_tile=resolve("image.clahe_tile", "IMAGE_CLAHE_TILE", int, 8),
            unsharp_amount=resolve("image.unsharp_amount", "IMAGE_UNSHARP_AMOUNT", float, 1.5),
            unsharp_sigma=resolve("image.unsharp_sigma", "IMAGE_UNSHARP_SIGMA", float, 3.0),
            deskew_min_angle=resolve("image.deskew_min_angle", "IMAGE_DESKEW_MIN_ANGLE", float, 3.0),
            deskew_min_area=resolve("image.deskew_min_area", "IMAGE_DESKEW_MIN_AREA", float, 0.40),
            deskew_max_area=resolve("image.deskew_max_area", "IMAGE_DESKEW_MAX_AREA", float, 0.97),
            deskew_open_kernel=resolve("image.deskew_open_kernel", "IMAGE_DESKEW_OPEN_KERNEL", int, 9),
            deskew_close_kernel=resolve("image.deskew_close_kernel", "IMAGE_DESKEW_CLOSE_KERNEL", int, 35),
            lowres_warn=resolve("image.lowres_warn", "PHOTO_LOWRES_WARN", int, 1500),
        )
        self.pdf = PDFSettings(
            render_dpi=resolve("pdf_to_image.render_dpi", "PDF_RENDER_DPI", int, 200),
            max_pages=resolve("pdf_to_image.max_pages", "MAX_PAGES", int, 50),
            text_layer_min_chars_per_page=int(__import__("os").getenv("TEXT_LAYER_MIN_CHARS_PER_PAGE", "50")),
            text_layer_y_tolerance=float(__import__("os").getenv("TEXT_LAYER_Y_TOLERANCE", "3.0")),
            verbatim_max_reject_ratio=float(__import__("os").getenv("VERBATIM_MAX_REJECT_RATIO", "0.5")),
            text_layer_temperature=float(__import__("os").getenv("TEXT_LAYER_TEMPERATURE", "0.0")),
            raw_log_limit=int(__import__("os").getenv("RAW_LOG_LIMIT", "4000")),
        )
        self.extraction = ExtractionSettings()
        self.normalize = NormalizeSettings(
            drug_max_edit_ratio=resolve("drugs.max_edit_ratio", "DRUG_MAX_EDIT_RATIO", float, 0.40),
            drug_ratio_floor=resolve("drugs.ratio_floor", "DRUG_RATIO_FLOOR", float, 70.0),
            analyte_max_edit_ratio=resolve("analytes.max_edit_ratio", "ANALYTE_MAX_EDIT_RATIO", float, 0.35),
            analyte_ratio_floor=resolve("analytes.ratio_floor", "ANALYTE_RATIO_FLOOR", float, 75.0),
            analyte_short_key_len=resolve("analytes.short_key_len", "ANALYTE_SHORT_KEY_LEN", int, 3),
        )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached AppSettings singleton."""
    return AppSettings()
