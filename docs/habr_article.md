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
hf download mlx-community/Qwen3-VL-8B-Instruct-4bit
```

**Важно:** для VLM (vision-language models) нужен именно `mlx-vlm`, а не `mlx-lm`. `mlx-lm.server` не поддерживает image content type — возвращает 404 на запросы с изображениями. `mlx_vlm.server` поддерживает.

**Server:**
```bash
python -m mlx_vlm.server \
    --model mlx-community/Qwen3-VL-8B-Instruct-4bit \
    --host 0.0.0.0 --port 8002
```

**Модель:** 4-bit квантизация (~5.4GB) — влезает в 32GB unified memory с запасом.

**Structured output:** нет native grammar constraint. `instructor.Mode.JSON` валидация + retry — модель может выдать невалидный JSON, instructor переспрашивает.

**Throughput:** ~49 tok/s decode, ~500 tok/s prefill (Apple Silicon, continuous batching).

**Плюсы:** нативный Metal backend для Apple Silicon, unified memory, 4-bit квантизация, continuous batching.

**Минусы:** нет structured output (grammar constraint), Mac only, classify медленнее Ollama (12.5s vs 6.5s на JPG).

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
| Ollama | Q4_K_M (~6GB) | XGrammar (`format`) | **35/35 (100%)** |
| MLX | 4-bit (~5.4GB) | instructor-only | **34/35 (97.1%)** |
| vLLM | bf16 (~16GB) | guided_json (outlines) | TBD (Windows) |

MLX не распознал 1 значение из 325 (sample_001.pdf: «Риск рака яичников ROMA» — вычисляемый показатель, модель его пропустила). Вероятная причина — 4-bit квантизация ухудшает качество на сложных вычисляемых полях.

### Скорость (Mac, 32GB Apple Silicon)

35 документов (20 PDF + 14 JPG + 1 синтетический), 325 эталонных значений.

| Backend | Wall time | Avg/doc | Classify total | Extract total | tok/s |
|---|---|---|---|---|---|
| Ollama | 912с (15:11) | 26.4с | 123.5с | 772.9с | ~30 |
| MLX | 938с (15:38) | 27.4с | 238.6с | 691.6с | ~49 |

**Классификация JPG (только image, без extract):**

| Backend | Avg classify JPG | 
|---|---|
| Ollama | 6.5с |
| MLX | 12.5с |

**Extract PDF с текстовым слоем (без VLM OCR):**

| Backend | Avg extract (text PDF) |
|---|---|
| Ollama | 22.0с |
| MLX | 18.5с |

**Extract PDF с VLM OCR (сканы, image-only):**

| Backend | sample_006 (20 values) | sample_007 |
|---|---|---|
| Ollama | 212с | 114с |
| MLX | 278с | 2.7с |

> vLLM на Mac не запущен: `torch==2.6.0` не доступен для macOS arm64. Metal backend экспериментальный.

**Выводы по скорости:**
- Ollama быстрее на классификации (6.5с vs 12.5с) — меньше overhead на image processing
- MLX быстрее на extract текстовых PDF (18.5с vs 22.0с) — выше decode rate (49 vs 30 tok/s)
- На сложных сканах (sample_006) Ollama быстрее (212с vs 278с) — XGrammar не даёт модели «расползаться»
- На простых сканах (sample_007) MLX резко быстрее (2.7с vs 114с) — модель быстрее понимает, что документ пустой

### Скорость (Windows, RTX 3080 16GB)

| Backend | Wall time | tok/s | Notes |
|---|---|---|---|
| Ollama (native) | TBD | TBD | |
| vLLM (WSL2) | TBD | TBD | |
| vLLM (Docker) | TBD | TBD | |

### Память

| Backend | Peak memory | Model size |
|---|---|---|
| Ollama | ~8GB (model + KV cache) | ~6GB (Q4_K_M) |
| MLX | ~7GB (model + KV cache) | ~5.4GB (4bit) |
| vLLM | ~14GB (model + PagedAttention) | ~16GB (bf16) |

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

- **Точность:** Ollama 35/35 (100%), MLX 34/35 (97.1%) — разница из-за 4-bit квантизации MLX
- **Скорость:** сопоставимо на Mac (912с vs 938с); MLX быстрее decode (49 vs 30 tok/s), Ollama быстрее classify
- **Удобство:** Ollama > vLLM > MLX (одна команда `ollama pull` vs `pip + hf download + server`)
- **VLM поддержка:** критично использовать `mlx-vlm`, а не `mlx-lm` — последний не принимает изображения
- **Production:** vLLM для нагрузки (NVIDIA), MLX для Mac (нативный Metal), Ollama для dev и CPU fallback

Конфигурируемость через одну переменную `LLM_BACKEND` позволяет переключаться между бэкендами без изменения кода — удобно для бенчмарка и для адаптации под доступное железо.
