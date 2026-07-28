"""LLM-related settings: VLM, text model, Ollama connection."""

from pydantic import BaseModel


class VLMSettings(BaseModel):
    """Vision-language model parameters for OCR extraction."""
    model: str = "qwen3-vl:8b-instruct"
    temperature: float = 0.0
    classify_temperature: float = 0.1
    num_ctx: int = 16384
    max_tokens: int = 8192
    num_predict: int = 8192
    repeat_penalty: float = 1.2
    structured_output: bool = True
    max_retries: int = 2
    retry_max_seconds: float = 300.0
    retry_initial_wait: float = 1.0
    retry_max_wait: float = 10.0
    request_timeout: float = 120.0


class TextModelSettings(BaseModel):
    """Text-only model for PDF text layer extraction."""
    model: str = "qwen3-vl:8b-instruct"
    temperature: float = 0.0
    num_ctx: int = 4096
    max_tokens: int = 8192
    num_predict: int = 8192
    repeat_penalty: float = 1.2
    structured_output: bool = True
    compact_output: bool = True


class OllamaSettings(BaseModel):
    """Ollama server connection and warmup parameters."""
    url: str = "http://localhost:11434"
    keep_alive: str = "30m"
    probe_timeout: float = 1.5
    wsl_detect_timeout: float = 5.0
    warmup_timeout: float = 300.0


class BackendSettings(BaseModel):
    """Configurable LLM inference backend."""
    backend: str = "ollama"
    vllm_url: str = "http://localhost:8001"
    mlx_url: str = "http://localhost:8002"
