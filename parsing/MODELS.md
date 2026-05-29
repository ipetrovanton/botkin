# Models — реальные замеры на ThinkPad RTX 3080 16 GB

> **Замерено:** 2026-05-28
> **Окружение:** WSL2 Ubuntu, Ollama 0.5.x, CUDA via WSL-passthrough
> **Команда запуска:** `uv run python -m botkin.cli all`

## 🎯 Финальная модель (выбрана после 3 итераций)

**`huihui_ai/qwen3-abliterated:14b`** — uncensored Qwen3 14B

| Параметр | Значение |
|----------|----------|
| **Метод uncensored** | abliteration (математическое удаление refusal-направлений) |
| **Размер** | 9.0 GB |
| **VRAM** | ~9 GB + ~1.5 GB KV cache |
| **TTFT** | 5.95 s |
| **Throughput** | **23.4 t/s** |
| **Thinking mode** | ✅ `/think`, `/no_think` |
| **Tools** | ✅ function calling |
| **Цензура** | 0/4 отказов на провокационные темы |

### Команды для работы с моделью

```bash
# Проверка подключения
uv run python -m botkin.cli check

# Запуск всех стандартных тестов
uv run python -m botkin.cli all-tests

# Базовый тест LLM
uv run python tests/smoke_test.py

# Тест на uncensored behavior
uv run python tests/test_uncensored.py

# Тест эмбеддингов
uv run python tests/smoke_embed.py

# Тест VLM hot-swap
uv run python tests/smoke_vlm.py

# Проверка списка моделей Ollama
wsl -u root -e bash -c "ollama list"
```

## 📋 История поиска модели

| Модель | Размер | Скорость | Цензура | Результат |
|--------|--------|----------|---------|-----------|
| `qwen3:14b` | 9.3 GB | 22.7 t/s | ❌ зацензурирован | Отброшена |
| `fredrezones55/Qwen3.6-27B-Uncensored:IQ4_XS` | 16 GB | ~3-5 t/s | ✅ uncensored | Слишком медленно |
| `huihui_ai/qwen3-abliterated:14b` | 9 GB | **23.4 t/s** | ✅ uncensored | ✅ **ФИНАЛ** |

## Требования

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) для управления зависимостями
- Ollama в WSL2 с поддержкой CUDA
- 16GB+ VRAM для Qwen3-14B

## Установка

```bash
# 1. Установка зависимостей
uv sync

# 2. Проверка подключения
uv run python -m botkin.cli check

# 3. Запуск всех тестов
uv run python -m botkin.cli all-tests
```

## Конфигурация через env-переменные

```bash
# Модели (можно менять без правки кода)
export OLLAMA_CHAT_MODEL="huihui_ai/qwen3-abliterated:14b"
export OLLAMA_VLM_MODEL="qwen3-vl:8b"
export OLLAMA_EMBED_MODEL="bge-m3"

# Параметры генерации
export OLLAMA_CHAT_TEMP="0.1"      # температура чат-модели
export OLLAMA_CHAT_CTX="4096"      # размер контекста
export OLLAMA_VLM_TEMP="0.2"       # температура VLM
export OLLAMA_VLM_CTX="2048"       # контекст VLM

# Ограничение VRAM (для больших моделей)
export OLLAMA_CHAT_NUM_GPU=25      # количество слоёв на GPU (~8GB VRAM)

# Подключение
export OLLAMA_URL="http://localhost:11434"
```

## huihui_ai/qwen3-abliterated:14b (chat + extract) — ФИНАЛЬНАЯ МОДЕЛЬ

| Параметр | Значение |
|----------|----------|
| **Тег** | `huihui_ai/qwen3-abliterated:14b` |
| **Размер** | 9.0 GB |
| **VRAM резидентно** | ~9.0 GB + 1.5 GB KV cache @ 4K ctx |
| **TTFT (Time To First Token)** | 5.95 s (с загрузкой модели) |
| **Throughput** | **23.4 t/s** ✅ |
| **Цензура** | **0%** (abliteration) |
| **Thinking mode** | ✅ поддерживается |

### Команды тестирования

```bash
# Базовый тест с параметрами по умолчанию
uv run python tests/smoke_test.py

# Тест на uncensored behavior (провокационные темы)
uv run python tests/test_uncensored.py

# С кастомной температурой и контекстом
uv run python -m botkin.cli chat --temp 0.5 --ctx 8192

# Только замер скорости
uv run python -c "
from botkin.client import OllamaClient
from botkin.config import OllamaConfig

config = OllamaConfig.from_env()
with OllamaClient(config) as c:
    _, m = c.generate('Тестовый промпт')
    print(f'TTFT: {m.time_to_first_token_sec:.2f}s, {m.tokens_per_second:.1f} t/s')
"
```

### Uncensored тест

Файл: `tests/test_uncensored.py`

Тестирует 4 провокационных темы:
1. Шифрование и криптоанализ
2. Фармакология наркотических веществ
3. SQL injection уязвимости
4. Сравнительный анализ пропаганды

Результат: **0/4 отказов** — модель отвечает без цензуры.

### Thinking mode

Qwen3 поддерживает режим размышлений:
- `think=True` — явное рассуждение, медленнее (~14 t/s)
- `think=False` — быстрый ответ, ~16-23 t/s

## Qwen3-VL-8B (vision, fallback для рукописи)

| Параметр | Значение |
|----------|----------|
| **Тег** | `qwen3-vl:8b` |
| **Размер** | 6.1 GB |
| **VRAM** | ~5.0 GB при инференсе |
| **TTFT** | 42.91 s (включая загрузку модели) |
| **Swap-cycle** | **68.7 s** ⚠️ (целевой ≤60 с) |

### Hot-swap паттерн

```python
from botkin.client import OllamaClient
from botkin.config import OllamaConfig

config = OllamaConfig.from_env()

with OllamaClient(config) as client:
    # 1. Работаем с LLM
    response, _ = client.generate("Вопрос")

    # 2. Выгружаем LLM перед VLM
    client.unload_model(config.chat_model.name)

    # 3. Загружаем VLM с изображением
    from botkin.client import image_to_b64
    img = image_to_b64("document.jpg")
    response, _ = client.generate(
        "Опиши документ",
        model_config=config.vlm_model,
        images=[img]
    )

    # 4. VLM выгрузится сам (keep_alive=0)
    # LLM загрузится при следующем запросе
```

## BGE-M3 (embeddings)

| Параметр | Значение |
|----------|----------|
| **Тег** | `bge-m3` |
| **Размер** | 1.2 GB |
| **Dimension** | 1024 ✅ |
| **VRAM** | ~1.5 GB при загруженной модели |
| **Throughput** | **250 chunks/min** ⚠️ (целевой 1000) |

### Проверка на NaN (известная проблема ollama#13572)

```bash
uv run python tests/smoke_embed.py
```

Если NaN обнаружены — чанки обрезаются до 512 токенов перед эмбеддингом.

## VRAM-бюджет

| Компонент | VRAM |
|-----------|------|
| Qwen3-14B (резидентно) | 9.0 GB |
| KV-cache 4K | 1.5 GB |
| BGE-M3 | 1.5 GB |
| ОС + драйверы | ~1.0 GB |
| **Всего активно** | **~13 GB / 16 GB** |
| **Запас** | ~3 GB |

## План Б при нехватке VRAM

1. **Уменьшить контекст:** `OLLAMA_CHAT_CTX=2048` экономит ~0.8 GB
2. **Перейти на Qwen3-7B:** `ollama pull qwen3:7b` (~4 GB вместо 9 GB)
3. **Отключить параллельные эмбеддинги** во время extract

## Troubleshooting

### Модель работает на CPU (медленно)

```bash
# Проверка GPU-использования
nvidia-smi

# Должно показывать процесс ollama с VRAM ~9 GB
# Если нет — проверить логи:
journalctl --user -u ollama -n 50 | grep -i "gpu\|cuda\|error"
```

### Не загружается модель

```bash
# Проверить, что модель существует
wsl -u root -e bash -c "ollama list"

# Если нет — загрузить
wsl -u root -e bash -c "ollama pull huihui_ai/qwen3-abliterated:14b"
wsl -u root -e bash -c "ollama pull qwen3-vl:8b"
wsl -u root -e bash -c "ollama pull bge-m3"
```

### Запуск Python скриптов (краткая справка)

```bash
# Все команды запускаются из корня проекта c:\Sandbox\botkin

# Через uv (рекомендуется)
uv run python tests/smoke_test.py
uv run python -m botkin.cli check

# Напрямую через python (если зависимости установлены)
python tests/smoke_test.py

# В WSL (для ollama-команд)
wsl -u root -e bash -c "ollama list"
wsl -u root -e bash -c "ollama ps"
```

### NaN в эмбеддингах

Известная проблема с BGE-M3 на длинных текстах. Решение:
- Обрезать чанки до 512 токенов
- Или использовать `nomic-embed-text` (768-dim)
