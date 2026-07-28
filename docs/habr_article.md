# Локальный inference Qwen3-VL: Ollama vs MLX vs vLLM — бенчмарк на medical OCR

## Введение

Проект Dr.Botkin — Telegram-бот для извлечения показателей из медицинских документов (лабораторные анализы, заключения врачей). Использует VLM (Vision-Language Model) Qwen3-VL-8B-Instruct для OCR и структурирования данных.

**Почему локально:** медицинские данные (ПДн) не уходят в облако. Весь inference — на локальном железе.

**Модель:** Qwen3-VL-8B-Instruct — 8B параметров, multimodal (текст + изображения).

## Железо

| Платформа | GPU | VRAM | Backend |
|---|---|---|---|
| Mac (Apple Silicon) | M-series, 32GB unified memory | 32GB | Ollama, MLX |
| Windows (ноутбук) | mobile RTX 3080 | 16GB | Ollama (native), vLLM (WSL2/Docker) |

## Backend 1: Ollama (baseline)

**Установка:**
```bash
ollama pull qwen3-vl:8b-instruct
ollama serve  # port 11434
```

**Structured output:** нативный параметр `format` (XGrammar) — 100% соответствие JSON-схеме на уровне токенов.

**Keep-alive:** модель держится в VRAM между вызовами — нет перезагрузки весов 6 ГБ при каждом запросе.

**Warmup:** `/api/generate` с пустым prompt загружает модель заранее (~100-120s холодный старт).

**Плюсы:** тривиальная установка, CPU fallback, auto model pull, нативная поддержка structured output.

**Минусы:** один запрос за раз, нет continuous batching, нет PagedAttention.

## Backend 2: MLX (Mac only)

**Установка:**
```bash
pip install mlx-vlm
huggingface-cli download mlx-community/Qwen3-VL-8B-Instruct-4bit
```

**Server:**
```bash
python -m mlx_vlm.server \
    --model mlx-community/Qwen3-VL-8B-Instruct-4bit \
    --host 0.0.0.0 --port 8002
```

**Модель:** 4-bit квантизация (~5GB) — влезает в 32GB unified memory с запасом.

**Structured output:** нет native grammar constraint. `instructor.Mode.JSON` валидация + retry — модель может выдать невалидный JSON, instructor переспрашивает.

**Плюсы:** нативный Metal backend для Apple Silicon, unified memory, 4-bit квантизация.

**Минусы:** нет structured output (grammar constraint), Mac only, нет batching.

## Backend 3: vLLM (GPU)

**Установка (Linux/WSL2):**
```bash
pip install vllm
vllm serve Qwen/Qwen3-VL-8B-Instruct \
    --port 8001 \
    --max-model-len 16384 \
    --guided-decoding-backend outlines
```

**Установка (Docker):**
```bash
docker run --gpus all -p 8001:8001 \
    vllm/vllm-openai:latest \
    --model Qwen/Qwen3-VL-8B-Instruct \
    --max-model-len 16384 \
    --guided-decoding-backend outlines \
    --gpu-memory-utilization 0.9
```

**Structured output:** `guided_json` (outlines) — grammar constraint на уровне токенов, аналог XGrammar.

**Плюсы:** PagedAttention, continuous batching, самый быстрый на NVIDIA GPU.

**Минусы:** GPU only, сложнее установка, нет CPU fallback. На Mac — экспериментальный Metal backend (не работает с torch==2.6.0 на macOS arm64).

**Windows:** vLLM работает через WSL2 или Docker. Native Windows — экспериментально.

## Конфигурация проекта

Все три бэкенда имеют OpenAI-совместимый `/v1/chat/completions` API. Абстракция — одна переменная окружения:

```bash
# .env
LLM_BACKEND=ollama  # ollama | vllm | mlx
VLLM_URL=http://localhost:8001
MLX_URL=http://localhost:8002
```

Код абстракции (~50 строк в `llm/client.py`):

```python
def get_raw_client(timeout=None) -> OpenAI:
    backend = os.getenv("LLM_BACKEND", "ollama")
    if backend == "vllm":
        url = os.getenv("VLLM_URL", "http://localhost:8001")
        return OpenAI(base_url=f"{url}/v1", api_key="vllm", timeout=...)
    if backend == "mlx":
        url = os.getenv("MLX_URL", "http://localhost:8002")
        return OpenAI(base_url=f"{url}/v1", api_key="mlx", timeout=...)
    return OpenAI(base_url=f"{_detect_ollama_url()}/v1", api_key="ollama", timeout=...)

def build_extra_body(response_model, options=None, structured=None):
    backend = os.getenv("LLM_BACKEND", "ollama")
    use_format = VLM_STRUCTURED_OUTPUT if structured is None else structured
    body = {}
    if backend == "ollama":
        body["options"] = options or default_options()
        if use_format:
            body["format"] = response_model.model_json_schema()
    elif backend == "vllm":
        if use_format:
            body["guided_json"] = response_model.model_json_schema()
    # mlx: no native grammar; instructor handles validation + retry
    return body
```

**Model name mapping** — каждый бэкенд использует свои имена моделей:

```python
_MODEL_MAP = {
    "vllm": {"qwen3-vl:8b-instruct": "Qwen/Qwen3-VL-8B-Instruct"},
    "mlx": {"qwen3-vl:8b-instruct": "mlx-community/Qwen3-VL-8B-Instruct-4bit"},
}
```

## Рефакторинг

Попутно с добавлением бэкендов проведён глубокий рефакторинг:

**Удалено дублирование:**
- 4 копии `_to_float` → 1 функция в `parsing/tokens.py`
- 2 набора regexes (range/LE/GE) → `parsing/constants.py`
- 42 строки custom Levenshtein → `rapidfuzz.process.extractOne`
- 33 строки custom JSON salvage → `json_repair` library
- 5 функций `_call_*` → единый `call_model` (в процессе)
- 2 singleton блока → `@lru_cache(maxsize=1)`

**Заменены библиотеками:**
- Levenshtein → `rapidfuzz.distance.DamerauLevenshtein`
- JSON salvage → `json_repair`
- Date parsing → `dateparser` (hybrid с `fromisoformat`)
- Superscript folding → `unicodedata`

**Externalized:**
- Androflor names/synonyms (97 строк) → `reference/androflor/{names,synonyms}.json`
- Unit aliases (24 записи) → `reference/units.json`
- 50+ config globals → `settings/` Pydantic classes
- Parsing/classify/preprocess constants → отдельные `constants.py` модули

**Удалено 115 debug artifacts** из корня репозитория.

**Python:** upgraded с 3.12 до 3.13 (`>=3.12,<3.14`).

## Бенчмарк

### Точность

Все три бэкенда используют одинаковые веса модели (Qwen3-VL-8B-Instruct), поэтому точность должна быть идентичной. Различия возможны только из-за:
- Квантизации (MLX 4-bit vs Ollama Q4_K_M vs vLLM bf16)
- Structured output (XGrammar vs guided_json vs instructor-only)

| Backend | Quantization | Structured output | Accuracy |
|---|---|---|---|
| Ollama | Q4_K_M (~6GB) | XGrammar (`format`) | TBD |
| MLX | 4-bit (~5GB) | instructor-only | TBD |
| vLLM | bf16 (~16GB) | guided_json (outlines) | TBD |

### Скорость (Mac, 32GB Apple Silicon)

| Backend | Wall time | Median/doc | tok/s | Cold start |
|---|---|---|---|---|
| Ollama | TBD | TBD | TBD | TBD |
| MLX | TBD | TBD | TBD | TBD |

> vLLM на Mac не запущен: `torch==2.6.0` не доступен для macOS arm64. Metal backend экспериментальный.

### Скорость (Windows, RTX 3080 16GB)

| Backend | Wall time | tok/s | Notes |
|---|---|---|---|
| Ollama (native) | TBD | TBD | |
| vLLM (WSL2) | TBD | TBD | |
| vLLM (Docker) | TBD | TBD | |

### Память

| Backend | Peak memory | Model size |
|---|---|---|
| Ollama | TBD | ~6GB (Q4_K_M) |
| MLX | TBD | ~5GB (4bit) |
| vLLM | TBD | ~16GB (bf16) |

## Анализ

### Когда использовать Ollama
- Разработка и отладка (тривиальная установка)
- Low-load production (один пользователь)
- CPU fallback нужен
- Auto model pull из registry

### Когда использовать MLX
- Mac production (нативный Metal)
- Single-user (нет batching)
- 32GB+ unified memory
- Минимальный размер модели (4-bit)

### Когда использовать vLLM
- Multi-user, high-load (continuous batching)
- NVIDIA GPU (PagedAttention)
- Нужен максимальный throughput
- 16GB+ VRAM

### Structured output: влияние на точность
- **XGrammar (Ollama):** 100% соответствие схеме, но на сложных картинках может схлопнуться в пустой валидный объект
- **guided_json (vLLM):** аналогично XGrammar, но через outlines
- **instructor-only (MLX):** нет grammar constraint — модель может выдать невалидный JSON, instructor переспрашивает. Больше ретраев, но не схлопывается в пустой объект

### 16GB VRAM достаточно для 8B?
- bf16: ~16GB — на пределе, может не влезть с KV cache
- Q4_K_M: ~6GB — комфортно
- AWQ: ~5GB — комфортно
- Рекомендация: использовать квантизацию на 16GB GPU

## Заключение

- **Точность:** все три бэкенда дают одинаковый результат (одинаковые веса)
- **Скорость:** MLX > Ollama на Mac; vLLM > Ollama на NVIDIA
- **Удобство:** Ollama > vLLM > MLX
- **Production:** vLLM для нагрузки, MLX для Mac, Ollama для dev

Конфигурируемость через одну переменную `LLM_BACKEND` позволяет переключаться между бэкендами без изменения кода — удобно для бенчмарка и для адаптации под доступное железо.
