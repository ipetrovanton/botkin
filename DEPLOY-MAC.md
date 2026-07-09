# Промт для развёртывания Botkin на macOS

> Этот файл — инструкция для AI-ассистента (Devin/Cursor/Claude) или человека,
> который разворачивает проект Botkin на Mac с нуля. Включает: установку Ollama,
> загрузку всех LLM, настройку RAG, загрузку реестров ГРЛС/ФСЛИ, импорт тестовых
> данных и запуск бенчмарков.

---

## Контекст

**Botkin** — локальная медицинская система: OCR/VLM-распознавание лабораторных
анализов + RAG-рекомендации через uncensored-LLM + синк Garmin/Apple Health.

**Целевая платформа:** macOS (Apple Silicon M-series или Intel Mac с GPU).
**Важно:** на Mac будет проведён **повторный замер скорости и точности** LLM —
это даст данные для сравнения с RTX 3080 Laptop (Windows) и улучшит статью.

## Предварительные требования

```bash
# 1. Homebrew (если нет)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Python 3.12 (строго — pyproject.toml требует >=3.12,<3.13)
brew install python@3.12

# 3. Ollama для macOS
# Скачай и установи: https://ollama.com/download/mac
# Или через brew:
brew install ollama
brew services start ollama

# 4. uv (менеджер пакетов — проект использует uv.lock)
brew install uv

# 5. Git LFS (для больших файлов — PDF-фикстуры, БД)
brew install git-lfs
git lfs install
```

## Шаг 1: Клонирование репозитория

```bash
git clone <repo-url> botkin
cd botkin

# Если репозиторий использует LFS для data/ и tests/fixtures/:
git lfs pull
```

## Шаг 2: Установка зависимостей

```bash
# uv создаст .venv и установит все зависимости из uv.lock
uv sync

# Активация (для ручных команд)
source .venv/bin/activate
```

**Важно для macOS:**
- `sqlite-vec` имеет wheel для macOS arm64 — проблем быть не должно.
- `pymupdf` (fitz) — есть wheel для macOS.
- `opencv-python-headless` — есть wheel для macOS.
- `pillow-heif` — может потребовать `brew install libheif`.
- `garminconnect` — pure Python, проблем нет.

Если `pillow-heif` не ставится:
```bash
brew install libheif
uv sync
```

## Шаг 3: Загрузка LLM-моделей в Ollama

### 3.1. Основная VLM-модель (OCR документов)

```bash
ollama pull qwen3-vl:8b-instruct      # ~5 GB, основная модель распознавания
```

### 3.2. Embedding-модель (для RAG)

```bash
ollama pull bge-m3                     # ~1.2 GB, мультиязычные эмбеддинги
```

### 3.3. Текстовая модель (RAG-рекомендации, бейслайн)

```bash
ollama pull qwen3:8b                   # ~5.2 GB, цензурный бейслайн
```

### 3.4. Uncensored-модели для бенчмарка

```bash
# MoE — быстрая, 24 GB
ollama pull huihui_ai/Qwen3.6-abliterated:35b-a3b

# GLM без цензуры, 19 GB
ollama pull huihui_ai/glm-4.7-flash-abliterated:q4_K

# Медицинская специализированная, 6.7 GB
ollama pull goekdenizguelmez/JOSIEFIED-Qwen3:8b-health-q6_k

# Reasoning (DeepSeek-R1 abliterated), 5 GB
ollama pull huihui_ai/deepseek-r1-abliterated:8b-0528-qwen3

# Dense 27B — самая медленная, 17 GB
ollama pull huihui_ai/Qwen3.6-abliterated:27b
```

### 3.5. Модели для OCR-бенчмарка (опционально)

```bash
ollama pull haervwe/GLM-4.6V-Flash-9B    # ~9 GB, кандидат №2
# qwen3.5:9b — если доступна
```

### 3.6. Проверка

```bash
ollama list
# Должно быть: qwen3-vl:8b-instruct, bge-m3, qwen3:8b,
# huihui_ai/Qwen3.6-abliterated:35b-a3b, и т.д.
```

**Важно для macOS (Apple Silicon):**
- Unified memory: VRAM = общая RAM. Модели до ~40 GB влезут в 32GB Mac,
  до ~70 GB — в 64GB Mac.
- Ollama на Mac использует Metal framework — скорость может отличаться от CUDA.
- Проверь: `ollama ps` — покажет, какие модели загружены в память.

## Шаг 4: Настройка конфигурации

```bash
# Создай .env в корне проекта
cat > .env << 'EOF'
# Telegram-бот (опционально для бенчмарка)
BOT_TOKEN=your_telegram_bot_token

# Ollama (по умолчанию localhost:11434 — на Mac работает из коробки)
OLLAMA_HOST=http://localhost:11434

# RAG
RAG_RECOMMEND_MODEL=qwen3:8b
RAG_WEB_ENABLED=false

# База данных
BOTKIN_DB_PATH=./data/botkin.db
EOF
```

## Шаг 5: Инициализация базы данных

```bash
# Если data/botkin.db уже в репозитории (через LFS) — пропусти этот шаг.
# Если нет — создай с нуля:
uv run python -c "from botkin.db.connection import init_db; init_db()"
```

## Шаг 6: Загрузка реестров ГРЛС/ФСЛИ

Реестры уже собраны в `src/botkin/reference/` (в репозитории):
- `src/botkin/reference/drugs/registry.jsonl` — 20 948 лекарств из ГРЛС
- `src/botkin/reference/analytes/registry.jsonl` — справочник показателей ФСЛИ

Если нужно пересобрать из свежей выгрузки ГРЛС:
```bash
# Скачай ZIP-выгрузку с grls.rosminzdrav.ru
uv run python -m scripts.build_drug_reference \
    --src grls.zip --out src/botkin/reference/drugs/registry.jsonl

uv run python -m scripts.build_analyte_reference \
    --src fsli.xlsx --out src/botkin/reference/analytes/registry.jsonl
```

## Шаг 7: Заполнение RAG-индекса

```bash
# 7.1. Индексация справочников (drugs + analytes) в векторную БД
uv run python -c "
from botkin.rag.store import RAGStore
from botkin.reference.drugs import load_drugs
from botkin.reference.analytes import load_analytes
store = RAGStore()
store.index_drugs(load_drugs())
store.index_analytes(load_analytes())
print('Справочники проиндексированы')
"

# 7.2. Загрузка свежих публикаций PubMed (research-RAG)
uv run python scripts/update_medical_research.py
# Ожидаемый результат: ~79 статей по 6 темам в source='research'
```

## Шаг 8: Тестовые данные

### 8.1. Документы для OCR-бенчмарка

В репозитории (через LFS): `tests/fixtures/documents/samples/`
- 20 PDF-бланков анализов + sidecar-эталоны `.expected.json`

Если файлов нет (репозиторий без LFS):
```bash
git lfs pull --include="tests/fixtures/documents/samples/"
```

### 8.2. Демо-пациент для RAG-бенчмарка

Данные пациента (user_id=1) уже в `data/botkin.db` (если БД в репозитории).
Если БД создана с нуля — нужно загрузить тестовые анализы:

```bash
# Импорт тестовых PDF через API/бота, или напрямую в БД:
# (потребует запущенный Ollama с qwen3-vl)
uv run uvicorn botkin.api:app --reload &
# Загрузи PDF через веб-кабинет: http://localhost:8000
# Или через API:
curl -X POST http://localhost:8000/api/upload \
    -F "file=@tests/fixtures/documents/samples/sample_001.pdf"
```

### 8.3. Health-данные (Garmin)

```bash
# Garmin-токены в data/health_tokens/ (если в репозитории)
# Если нет — подключи через веб-кабинет: http://localhost:8000 → Настройки → Garmin
```

## Шаг 9: Запуск тестов

```bash
# Unit-тесты (без Ollama, быстро)
uv run pytest -q

# E2E тесты с реальной VLM (нужен Ollama + qwen3-vl)
uv run pytest -m llm -s

# Только Garmin/health
uv run pytest tests/test_health_api.py tests/test_health_repo.py tests/test_rag_store.py -v
```

## Шаг 10: Запуск бенчмарков

### 10.1. OCR-бенчмарк (точность распознавания)

```bash
# Все модели по умолчанию:
uv run python scripts/bench/bench_models.py --pull

# Или конкретные:
uv run python scripts/bench/bench_models.py --models qwen3-vl:8b-instruct

# «Ожидания vs реальность» (с публичными бенчмарками):
uv run python scripts/bench/bench_expectations.py --pull
```

### 10.2. RAG-бенчмарк (uncensored-модели)

```bash
# Только RAG (без веба):
uv run python -m scripts.bench.bench_uncensored_rag --web off

# RAG + веб (на вопросе differential):
uv run python -m scripts.bench.bench_uncensored_rag --web on --questions differential

# Анализ + графики:
uv run python -m scripts.bench.analyze_uncensored_rag --in habr/bench-uncensored
```

### 10.3. Сохранение результатов для статьи

```bash
# Результаты сохраняются в:
# habr/bench-uncensored/results.json + results.md + analysis.md + chart_*.png
# scripts/bench/bench_expectations_results.json + bench_expectations_report.md

# Для сравнения с Windows-результатами — переименуй:
cp -r habr/bench-uncensored habr/bench-uncensored-mac
```

## Шаг 11: Запуск приложения

```bash
# API + веб-кабинет
uv run uvicorn botkin.api:app --host 0.0.0.0 --port 8000

# Telegram-бот (опционально)
uv run python -m botkin.bot
```

Веб-кабинет: http://localhost:8000

---

## Отличия macOS от Windows (важно для статьи)

| Аспект | Windows (RTX 3080) | macOS (Apple Silicon) |
|---|---|---|
| GPU | CUDA, 16GB VRAM | Metal, unified memory (RAM = VRAM) |
| Оффлоад | В RAM (медленно, DDR4/5) | В unified memory (быстро, LPDDR5) |
| sqlite-vec | win_amd64 wheel | macos arm64 wheel |
| Ollama | Native Windows или WSL2 | Native macOS |
| Шрифты для графиков | C:\Windows\Fonts\segoeui.ttf | /System/Library/Fonts/ |
| Кодировка | PYTHONUTF8=1 (обязательно) | UTF-8 по умолчанию |
| Прогрев моделей | `/api/generate` empty prompt | То же |

**Для графиков на Mac** — поправь путь к шрифту в `analyze_uncensored_rag.py`:
```python
# Замени C:\Windows\Fonts\segoeui.ttf на:
"/System/Library/Fonts/Helvetica.ttc"
# или
"/Library/Fonts/Arial.ttf"
```

## Чек-лист готовности

- [ ] Ollama установлена и запущена (`ollama list` показывает модели)
- [ ] `uv sync` прошёл без ошибок
- [ ] `uv run pytest -q` — все тесты зелёные
- [ ] `data/botkin.db` существует и содержит данные (318 показателей user_id=1)
- [ ] RAG-индекс заполнен (drugs + analytes + research)
- [ ] `uv run python -m scripts.bench.bench_uncensored_rag --web off` отработал
- [ ] Графики сгенерированы в `habr/bench-uncensored-mac/`
- [ ] Результаты сохранены для сравнения в статье
