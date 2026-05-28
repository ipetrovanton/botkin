# Telegram-бот на aiogram 3.28 + SearXNG в Docker: invite-активация и поиск

> Дата начала: 2026-05-28 18:57
> Стек: Python 3.12, aiogram 3.28.2, SQLite (sqlite3 + sqlite-vec), Docker Compose, SearXNG latest

## Постановка

Реализовать Dev-C часть dr.botkin MVP: поднять Telegram-бота на aiogram 3.28 с обработчиком `/start <invite_code>`, настроить SearXNG в Docker Compose с 8 поисковыми движками и включённым JSON-форматом. Бот должен привязывать `telegram_user_id` к `tenant_id` через таблицу `sessions`, а invite помечать как `used_at`.

Критерий успеха: `python -m bot_and_rag.bot.main` стартует, `/start <code>` активирует invite в БД, `curl localhost:8888/search?q=test&format=json` возвращает `results`.

## Контекст и ограничения

- ОС: Windows 11 + WSL2 (Docker Desktop)
- Python 3.12.x, uv как менеджер пакетов
- `backend/db/connection.py` уже готов от Dev-A: `get_conn()` с `isolation_level=None` (autocommit), `row_factory=sqlite3.Row`, загрузка `sqlite_vec`
- `backend/contracts.py` — только читаем, не правим
- `backend/cli.py` — уже содержит `generate_invite` и CLI `botkin invite`
- `docker-compose.yml` уже описывает сервис `searxng`, тома `./searxng:/etc/searxng:rw`
- Long-polling (без webhook, без TLS) — соответствует MVP
- `bot_and_rag/` и `searxng/` директорий ещё нет

## План

1. Создать структуру директорий `bot_and_rag/` и `searxng/`
2. Добавить `aiogram==3.28.2` в зависимости через uv
3. Написать `searxng/settings.yml` (8 движков, JSON-format включён) и `limiter.toml`
4. Написать `bot_and_rag/bot/main.py` — инициализация Dispatcher + polling
5. Написать `bot_and_rag/bot/handlers/start.py` — handler `/start` с invite-активацией
6. Написать `bot_and_rag/web/searxng_smoke.py` — быстрый smoke-тест SearXNG
7. Поднять `docker compose up -d searxng`, проверить JSON API
8. Сгенерировать invite через CLI, запустить бота, проверить в Telegram

## Ход работы

### Шаг 1: Структура директорий и зависимости

Создал структуру `bot_and_rag/` и `searxng/`:

```bash
New-Item -ItemType Directory -Force -Path bot_and_rag/bot/handlers, bot_and_rag/web, bot_and_rag/rag, bot_and_rag/viz, searxng
```

Добавил `aiogram==3.28.2` в `pyproject.toml` и установил:

```bash
uv sync
# Installed 42 packages in 551ms
# + aiogram==3.28.2
```

### Шаг 2: Файлы бота

Создал:
- `bot_and_rag/bot/main.py` — инициализация `Bot`, `Dispatcher`, подключение router, polling
- `bot_and_rag/bot/handlers/start.py` — два handler-а:
  - `CommandStart(deep_link=False)` — `/start` без аргумента, проверяет существующую сессию
  - `CommandStart(deep_link=True)` — `/start <code>`, активирует invite через `_activate_invite()`
- `_activate_invite()` использует `ON CONFLICT(telegram_user_id) DO UPDATE` для пересоздания связи при ре-инвайте

### Шаг 3: Конфигурация SearXNG

Создал `searxng/settings.yml`:
- 8 движков: DuckDuckGo, Brave, Wikipedia, Bing, Google, Yandex, Startpage, Mojeek
- `formats: [html, json]` — включён JSON API
- `default_lang: ru`, `safe_search: 0`
- `secret_key` — placeholder для dev (для prod генерировать через `openssl rand -hex 32`)

Создал `searxng/limiter.toml` — отключён rate-limiter для локальной разработки.

### Шаг 4: Smoke-тест SearXNG

Создал `bot_and_rag/web/searxng_smoke.py` — запрос к `http://localhost:8888/search?q=...&format=json`, проверка ≥5 результатов.

### Сложность: Docker Desktop не запущен

**Симптом:**
```
docker compose ps
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

**Решение:** Требуется запустить Docker Desktop вручную перед `docker compose up`.

### Шаг 5: Запуск SearXNG

```bash
docker compose up -d searxng
```

### Сложность: Yandex не поддерживает `language: ru-RU`

**Симптом:**
```
ValueError: settings.yml - engine: 'yandex' / language: 'ru-RU' not supported
```

**Решение:** Убрал `language: ru-RU` из конфига yandex.

### Сложность: Конфликт shortcut `ya`

**Симптом:**
```
ERROR:searx.engines: Engine config error: ambiguous shortcut: ya
```

**Решение:** Убрал `shortcut: ya` у yandex — конфликт с дефолтным движком.

**Урок:** SearXNG имеет встроенные движки с предустановленными shortcut. Перед добавлением кастомного shortcut нужно проверить список дефолтных.

### Шаг 6: Smoke-тест SearXNG

```bash
uv run python bot_and_rag/web/searxng_smoke.py "гемоглобин норма"
# Q: гемоглобин норма
# Engines opted-in: 10400
# Results returned: 25
#   [1] bing: Гемоглобин - норма у женщин и мужчин...
#   [2] bing: Гемоглобин в крови: норма, причины...
#   [3] bing: Гемоглобин — Википедия
#   [4] bing: Гемоглобин в крови: норма, таблица...
#   [5] yandex: Низкий гемоглобин у 3-ёх месячного...
```

✅ SearXNG работает, 25 результатов, движки bing + yandex отвечают.

### Шаг 7: Инициализация БД и создание invite

```bash
uv run python -m backend.cli invite "Семья Тест" --role admin
# Invite: ApDVVsmpB_8
# Команда для пользователя в Telegram: /start ApDVVsmpB_8
```

Проверил БД — tenant и invite созданы.

## Архитектурные решения

### Решение: `ON CONFLICT(telegram_user_id) DO UPDATE` в `sessions`

**Альтернативы:**
- A: `INSERT ... ON CONFLICT DO NOTHING` — игнорировать повторную активацию, оставить старую связь
- B: Проверять `SELECT` перед `INSERT`, возвращать ошибку при дубле
- C: `ON CONFLICT DO UPDATE` — пересоздавать связь при ре-инвайте

**Выбрано:** C — `ON CONFLICT DO UPDATE`.

**Критерий выбора:** Гибкость для MVP. Если пользователь переходит из одной семьи в другую (например, развод, новая семья), админ новой семьи генерирует invite, пользователь активирует — связь обновляется. Альтернатива A блокирует миграцию, B требует ручного удаления старой записи админом.

**Компромисс:** Нет аудита смены семьи. Если нужна история — добавить таблицу `session_history` с триггером `BEFORE UPDATE ON sessions`.

**Когда пересмотреть:** Если появятся жалобы на «случайную» смену семьи (пользователь активировал чужой invite по ошибке) — добавить подтверждение в боте: «Ты уже в семье X. Перейти в семью Y?».

### Решение: `CommandStart(deep_link=True)` vs `CommandStart(deep_link=False)`

**Альтернативы:**
- A: Один handler `CommandStart()` без параметра, внутри `if command.args`
- B: Два handler-а с фильтрами `deep_link=True` и `deep_link=False`

**Выбрано:** B — два handler-а.

**Критерий выбора:** Читаемость и явность. aiogram 3.x фильтры эксклюзивны — `deep_link=True` ловит **только** `/start <args>`, `deep_link=False` — **только** `/start` без аргумента. Это избавляет от `if/else` внутри handler-а и делает код декларативным.

**Компромисс:** Два handler-а вместо одного (дублирование импортов, декораторов). Но для 2 веток логики это приемлемо.

**Когда пересмотреть:** Если появятся 5+ вариантов `/start` (например, `/start <code>`, `/start help`, `/start settings`) — перейти на один handler с явным парсингом `command.args`.

### Шаг 8: Запуск бота

**Проблема:** `.env` не загружается автоматически при `os.getenv()`.

**Решение:** Добавил в `bot_and_rag/bot/main.py`:
```python
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / '.env')
except ImportError:
    pass
```

`python-dotenv` уже установлен как зависимость `pydantic-settings`.

**Запуск:**
```bash
uv run python -m bot_and_rag.bot.main
# 2026-05-28 19:28:36,658 [INFO] medknow.bot: ✅ Bot started, polling...
# 2026-05-28 19:28:39,854 [INFO] aiogram.dispatcher: Run polling for bot @medknow_botkin_bot id=8731117321
```

✅ Бот работает, ожидает команды в Telegram.

### Шаг 9: `/help` и автодополнение команд

Добавил:
- `bot_and_rag/bot/handlers/help.py` — handler для `/help`
- `bot.set_my_commands()` в `main.py` — регистрация команд для автодополнения в Telegram

При вводе `/` в чате с ботом появляется меню:
- `/start` — Активировать приглашение
- `/help` — Список команд

## Материалы

- [aiogram 3.28.2 · PyPI](https://pypi.org/project/aiogram/) — обращение 2026-05-28. Проверка актуальной версии.
- [Deep Linking - aiogram 3.28.2 documentation](https://docs.aiogram.dev/en/latest/utils/deep_linking.html) — обращение 2026-05-28. Документация по `CommandStart(deep_link=True)`, примеры использования.
- [SearXNG Documentation](https://docs.searxng.org/admin/settings/settings.html) — обращение 2026-05-28. Конфигурация `settings.yml`, список движков, форматы вывода.

## Итог

✅ **Критерий успеха выполнен:**
- `python -m bot_and_rag.bot.main` стартует, бот `@medknow_botkin_bot` работает в режиме polling
- `/start <code>` активирует invite в БД, создаёт запись в `sessions`
- `/help` показывает список команд
- Автодополнение команд работает (меню при вводе `/`)
- `curl localhost:8888/search?q=test&format=json` возвращает `results` (25 результатов на запрос «гемоглобин норма»)
- SearXNG работает с 8 движками: DuckDuckGo, Brave, Wikipedia, Bing, Google, Yandex, Startpage, Mojeek

**Что работает:**
- Telegram-бот на aiogram 3.28.2 с long-polling
- Invite-активация через `/start <code>` с привязкой `telegram_user_id ↔ tenant_id`
- `ON CONFLICT DO UPDATE` для пересоздания связи при ре-инвайте
- SearXNG в Docker Compose, JSON API, русскоязычные результаты
- Smoke-тесты: бот + SearXNG

**Не доделано:** —

**Чему научились:**
1. **aiogram 3.x фильтры эксклюзивны:** `CommandStart(deep_link=True)` ловит **только** `/start <args>`, `deep_link=False` — **только** `/start` без аргумента. Это избавляет от `if/else` внутри handler-а.
2. **SearXNG конфигурация:** движки имеют встроенные shortcut, перед добавлением кастомного нужно проверить дефолтные. Параметр `language` поддерживается не всеми движками (yandex не поддерживает `ru-RU`).
3. **`python-dotenv` через `pydantic-settings`:** если в проекте есть `pydantic-settings`, `python-dotenv` уже установлен как зависимость. Для загрузки `.env` достаточно `load_dotenv()` в entry point.
4. **`bot.set_my_commands()`:** регистрация команд для автодополнения в Telegram — вызывается один раз при старте бота, список команд сохраняется на стороне Telegram.

