# Dr. Botkin — Telegram-бот для парсинга медицинских документов

Telegram-бот, который принимает фото и PDF медицинских документов (анализы, выписки, заключения),
распознаёт их через vision-модель **qwen3-vl:8b-instruct** (Ollama) и сохраняет
**нормализованные** структурированные данные в SQLite. Названия лекарств сверяются с офлайн-
справочником ГРЛС (исправление ошибок распознавания), сохраняются статусы регистрации и
оригинал извлечения. Умеет строить графики динамики показателей.

## Возможности

- Приём PDF и изображений (`.jpg`, `.png`, `.heic`, `.webp`) через Telegram
- Распознавание анализов и заключений врача через `qwen3-vl:8b-instruct` (рецепты пока не поддерживаются)
- **Нормализация данных:** даты → единый ISO, единицы → канон, числа (запятая → точка)
- **Коррекция названий лекарств** по справочнику ГРЛС (фаззи-матчинг, дистанция
  Дамерау-Левенштейна): `Элкап` → `Элькар`, `Глиалатин` → `Глиатилин` и т.п.; редкие/неизвестные
  препараты не подменяются (статус `unverified`)
- Заполнение МНН из торгового названия + сохранение статуса регистрации (действующий,
  исключён, приостановлен и т.д.) и номера РУ
- Сохранение **сырого извлечения** (`raw_extraction`) — данные восстановимы без потерь
- Хранение истории в SQLite с изоляцией данных пользователей
- Построение графиков динамики показателей (`/dynamics гемоглобин`), просмотр (`/last`, `/show`)

## Архитектура

```
┌───────────────┐     ┌─────────────────┐     ┌─────────┐
│  Telegram     │────▶│  FastAPI        │────▶│  SQLite │
│  Bot (aiogram)│     │  Backend (:8000)│     │         │
└───────────────┘     └───────┬─────────┘     └─────────┘
                              │
              classify → extract → normalize → persist
                              │                    │
                   ┌──────────▼─────────┐   ┌──────▼──────────────┐
                   │  Ollama (WSL2)     │   │ reference/drugs/    │
                   │  qwen3-vl:8b-instr │   │ registry.jsonl(ГРЛС)│
                   └────────────────────┘   └─────────────────────┘
```

## Требования

- Python 3.12, [uv](https://github.com/astral-sh/uv) — менеджер пакетов
- Ollama с моделью `qwen3-vl:8b-instruct` (важно: **instruct**-вариант, не thinking — он
  кратно быстрее; запущена в WSL2 или Linux)
- Токен Telegram-бота (получить у [@BotFather](https://t.me/BotFather))
- Справочник лекарств `src/botkin/reference/drugs/registry.jsonl` (уже в репозитории;
  пересборка — см. ниже)

## Запуск под Windows (WSL2 + Ollama)

Целевая конфигурация: ноутбук с NVIDIA GPU, Ollama в WSL2.

**1. Ollama в WSL2 и модель**

```bash
# внутри WSL2 (Ubuntu)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3-vl:8b-instruct
# проверить, что отвечает
curl http://localhost:11434/api/version
```

> Если бот запускается из Windows (вне WSL2), а Ollama — внутри WSL2, укажите в `.env`
> `OLLAMA_URL=http://<IP-WSL2>:11434` (узнать IP: `wsl hostname -I`). Бот также умеет
> автоопределять адрес WSL2 (см. `llm/client.py`).

**2. Проект (в WSL2 или Windows с установленным uv)**

```bash
git clone <repo-url>
cd botkin
uv sync                      # ставит зависимости в .venv (Python 3.12)
cp .env.example .env         # заполнить TG_BOT_TOKEN
```

**3. Запуск бэкенда и бота (два терминала)**

```bash
# терминал 1 — FastAPI backend
uv run uvicorn botkin.api.app:app --host 0.0.0.0 --port 8000

# терминал 2 — Telegram-бот
uv run python -m botkin.bot.main
```

База данных и таблицы создаются автоматически при старте (включая идемпотентную миграцию
новых колонок). Отправьте боту `/start`, затем фото или PDF документа.

## Справочник лекарств (ГРЛС)

Коррекция названий опирается на офлайн-справочник `src/botkin/reference/drugs/registry.jsonl`
(≈21 тыс. названий: торговые + МНН, со статусами и номерами РУ). Он уже собран и закоммичен.

Пересборка из свежей официальной выгрузки ГРЛС (ZIP с 8 листами-статусами):

```bash
uv run python -m scripts.build_drug_reference \
    --src grls2026-06-02-1.zip \
    --out src/botkin/reference/drugs/registry.jsonl
```

Сам ZIP в репозиторий не коммитится (он — вход скрипта, см. `.gitignore`).

## Конфигурация

Основные переменные в `.env`:

| Переменная       | Назначение                  | По умолчанию              |
|------------------|-----------------------------|---------------------------|
| `TG_BOT_TOKEN`   | Токен Telegram-бота         | *обязательно*             |
| `OLLAMA_URL`     | URL Ollama API              | `http://localhost:11434`  |
| `VLM_MODEL`      | Название vision-модели      | `qwen3-vl:8b-instruct`    |
| `SQLITE_PATH`    | Путь к файлу БД             | `./data/botkin.db`        |
| `API_URL`        | URL бэкенда (для бота)      | `http://localhost:8000`   |

Детальные параметры — в `config.json`: VLM (`temperature`, `num_ctx`, `num_predict`,
`repeat_penalty`), `ollama.keep_alive` (держит модель в VRAM между вызовами), подготовка
изображений (`render_dpi`, `max_long_side`, `jpeg_quality`, `classify_long_side`) и пороги
фаззи-коррекции (`drugs.max_edit_ratio`, `drugs.ratio_floor`). Эти значения — стартовые,
подбираются замером на целевой машине.

## Структура проекта

```
botkin/
├── src/botkin/
│   ├── api/                 # FastAPI: app.py, deps.py, routes/upload.py
│   ├── bot/                 # Telegram-бот (aiogram): main.py, handlers/
│   ├── db/                  # SQLite: connection.py (+миграция), schema.sql, queries.py, repos.py
│   ├── domain/models.py     # Pydantic-модели (+*_raw поля)
│   ├── llm/                 # Ollama: client.py, classify.py, extract.py, prompts.py
│   ├── preprocess/images.py # Подготовка PDF/фото к VLM (DPI, даунскейл, EXIF, HEIC)
│   ├── normalize/           # numbers.py, dates.py, units.py, drugs.py
│   ├── reference/           # units.py + drugs/registry.jsonl (справочник ГРЛС)
│   ├── pipeline/            # orchestrator.py (classify→extract→normalize→persist), notifications.py
│   ├── viz/plots.py         # Графики динамики (Plotly)
│   ├── config.py            # Централизованная конфигурация
│   └── exceptions.py        # Типизированные исключения
├── scripts/build_drug_reference.py  # Сборка registry.jsonl из выгрузки ГРЛС (openpyxl)
├── tests/                   # pytest (LLM мокается; модель в тестах не запускается)
├── config.json              # Детальные настройки
├── pyproject.toml           # Зависимости (uv)
└── .env.example             # Шаблон переменных окружения
```

## Команды бота

| Команда                        | Описание                               |
|--------------------------------|----------------------------------------|
| `/start`                       | Регистрация и приветствие              |
| `/help`                        | Справка по командам                    |
| `/show` или `/last`            | Последний загруженный документ         |
| `/dynamics <показатель>`       | График динамики показателя             |

## Разработка

```bash
uv run ruff check src tests scripts   # линтер
uv run pytest -q                      # тесты (без сети и GPU; LLM мокается)
```

## Лицензия

MIT
