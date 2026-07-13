# Справочник Разработки и Локального Запуска (AGENTS.md)

Файл содержит зафиксированную информацию по архитектуре проекта, командам сборки, локального запуска и схеме ручного/автоматического тестирования.

---

## 🛠️ 1. Системные требования и окружение

- **OS**: Windows 10/11 с установленной WSL2 (Ubuntu).
- **Python**: `>= 3.12` на Windows-хосте и в WSL2.
- **Движок моделей**: Ollama, запущенный внутри WSL2.
- **СУБД**: SQLite 3 встроенная в Python.

---

## 🚀 2. Схема и команды локального запуска

Для полноценной работы проекта и исключения сетевых конфликтов Windows <-> WSL2, запуск разделен на две части:

### Шаг А. Подготовка и запуск моделей (внутри WSL2)
1. Убедитесь, что служба Ollama запущена внутри WSL2 (Ubuntu) и слушает на всех интерфейсах (`OLLAMA_HOST=0.0.0.0:11434`):
   ```bash
   # Выполнить внутри WSL2 или как root-WSL с Windows-хоста
   sudo sed -i '/ExecStart/i Environment="OLLAMA_HOST=0.0.0.0:11434"' /etc/systemd/system/ollama.service
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```
2. Проверьте скачанные модели:
   ```bash
   ollama list
   # Ожидаются: qwen3-vl:8b, huihui_ai/qwen3-abliterated:14b, bge-m3:latest
   ```

### Шаг Б. Запуск бэкенда и Telegram-бота (на Windows-хосте)
1. Установите зависимости и пакет:
   ```powershell
   uv sync
   ```
2. Запустите FastAPI API-сервер:
   ```powershell
   uv run uvicorn botkin.api.app:app --host 0.0.0.0 --port 8000
   ```
3. В отдельном окне терминала запустите Telegram-бота:
   ```powershell
   # Убедитесь, что в .env прописан TG_BOT_TOKEN
   uv run python -m botkin.bot.main
   ```

### Шаг В. Веб-кабинет пациента (SPA, тот же FastAPI)

Веб-кабинет раздаётся тем же API-сервером из Шага Б — отдельный процесс не нужен:

```powershell
# Запустить API (если ещё не запущен)
uv run uvicorn botkin.api.app:app --host 0.0.0.0 --port 8000
# Открыть в браузере
start http://localhost:8000
```

Кабинет — SPA на Alpine.js (заендорено локально, без CDN/сборщика). Идентификация на demo-этапе
— через `X-Telegram-User-Id`: на экране «Профиль» введите идентификатор или нажмите
«Использовать demo (113521070)» (под этим пользователем в `data/botkin.db` есть реальные данные:
32 документа, 276 показателей, 9 заключений). Значение сохраняется в `localStorage`.

Экраны: Обзор (дашборд), Документы (фильтры: тип/клиника/врач/даты/статус/поиск + пагинация),
Загрузка (drag&drop + поллинг прогресса по стадиям), Аналитика (SVG-график динамики с коридором
нормы), Заключения (лента с фильтрами), детальная карточка документа. Тёмная тема по умолчанию,
переключатель в шапке. Mobile-first: нижняя навигация, `safe-area-inset` под notch.

API кабинета: `/api/me`, `/api/documents`, `/api/documents/{id}`, `/api/documents/{id}/status`,
`/api/analytes`, `/api/clinics`, `/api/doctors`, `/api/dynamics?name=`, `/api/labs/period`,
`/api/reports`, `/api/stats`. Все требуют заголовок `X-Telegram-User-Id`.

---

## 🤖 3. Инструкция по проверке функционала через Telegram-бота

После запуска бота, пользователь может выполнить следующие сценарии тестирования:

1. **Активация и старт**:
   - Отправьте боту команду `/start`. Бот автоматически зарегистрирует вас и покажет приветствие.
2. **Загрузка анализов / заключения врача**:
   - Отправьте боту PDF-документ или фото бланка (например, `sample_001.pdf` или `sample_030.jpg`).
   - Бот примет документ и запустит фоновую обработку. (Рецепты пока не поддерживаются — попадают в `unknown`.)
3. **Просмотр результатов и аналитики**:
   - **/last** или **/show**: Показывает результаты обработки последнего загруженного документа. Бот выведет тип (Анализы 🧪 или Заключение врача 👨‍⚕️), дату, а также структурированный список (показатели анализов с маркерами нормы ⬇️/⬆️, либо диагноз/рекомендации/назначения из заключения).
   - **/dynamics <название_показателя>**: Бот сгенерирует PNG-график динамики этого показателя на `plotly`, отрендерит референсный коридор нормы зеленым цветом и пришлет картинку. (Например, `/dynamics гемоглобин`).

---

## 🧹 4. Администрирование и очистка дедлоков (WSL2 / Ollama)

Если во время инференса тяжелых моделей (Qwen3-14B или Qwen3-VL) произошли сетевые зависания или прерывания, в фоне WSL2 могут зависнуть зомби-процессы питона, держащие сокеты. Для полной очистки выполните:
```powershell
# Убить фоновые процессы Python в WSL
wsl -u root -d Ubuntu pkill -f python3

# Перезапустить Ollama для очистки VRAM и очереди
wsl -u root -d Ubuntu systemctl restart ollama
```

---

## 📁 5. Структура проекта

```
botkin/
├── src/botkin/              # Пакет (устанавливаемый через uv/pip)
│   ├── api/                 # FastAPI-приложение
│   │   ├── app.py           # Точка входа сервера + mount статики SPA
│   │   ├── deps.py          # Зависимости (get_user_id)
│   │   └── routes/          # Роуты
│   │       ├── upload.py    # POST /upload (бот и веб-кабинет)
│   │       ├── documents.py # /api/* — лента, карточка, статус кабинета
│   │       └── analytics.py # /api/* — селекторы, динамика, периоды, статистика
│   ├── bot/                 # Telegram-бот (aiogram)
│   │   ├── main.py          # Точка входа бота
│   │   └── handlers/        # /start, /help, /show, /dynamics, upload
│   ├── db/                  # База данных
│   │   ├── connection.py    # Подключение, init_db, Python-lower для кириллицы
│   │   ├── schema.sql       # DDL-схема (5 таблиц)
│   │   ├── queries.py       # Аналитические запросы
│   │   └── repos.py         # Репозитории (DocumentRepo, LabRepo, ReportRepo, UserRepo)
│   ├── domain/              # Доменные модели
│   │   └── models.py        # LabResult, DoctorReport, ClassifyResult, etc.
│   ├── llm/                 # VLM-интеграция (qwen3-vl)
│   │   ├── client.py        # Ollama OpenAI-совместимый клиент
│   │   ├── classify.py      # Классификация документа
│   │   ├── extract.py       # Извлечение данных
│   │   └── prompts.py       # Все VLM-промпты
│   ├── pipeline/            # Пайплайн обработки
│   │   ├── orchestrator.py  # classify → extract → persist
│   │   └── notifications.py # Telegram-уведомления
│   ├── viz/                 # Визуализация
│   │   └── plots.py         # Plotly-графики динамики
│   ├── web/                 # Веб-кабинет пациента (SPA, без сборщика)
│   │   ├── index.html       # Каркас на Alpine.js: 7 экранов + bottom-nav
│   │   ├── styles.css       # Дизайн-система: бренд, темы, анимации, SVG
│   │   ├── app.js           # Компонент cabinet(): API-клиент, экраны, график
│   │   └── vendor/alpine.min.js  # Alpine.js 3.15.12 (MIT, заендорено локально)
│   ├── config.py            # Централизованная конфигурация
│   └── exceptions.py        # Типизированные исключения
├── tests/                   # Тесты
│   ├── conftest.py          # Фикстуры
│   ├── test_cabinet_repo.py # 12 тестов репозиториев кабинета
│   ├── test_cabinet_api.py  # 13 тестов /api/* через TestClient
│   └── test_smoke.py        # smoke-тесты
├── config.json              # Переопределения конфигурации
├── pyproject.toml           # Зависимости, entry points, tool config
├── .env.example             # Шаблон переменных окружения
├── AGENTS.md                # Этот файл
├── LICENSE                  # MIT
└── README.md
```

### Индексы Базы Данных для Оптимизации Производительности:
Для ускорения SQL-запросов в схему `src/botkin/db/schema.sql` добавлены индексы:
- `idx_documents_user` на `documents(user_id)`
- `idx_documents_status` на `documents(status)`
- `idx_documents_user_created` на `documents(user_id, created_at)` — лента `/list` и навигация по соседям карточки
- `idx_lab_user_analyte` на `lab_results(user_id, analyte_name, taken_at)`
- `idx_doctor_reports_user` на `doctor_reports(user_id, visit_date)`
- `idx_doctor_reports_document` на `doctor_reports(document_id)` — оптимизирует `/show`

---

## 6. Запуск e2e-тестов (LLM) с Ollama в WSL2

### Проблема: Python из Windows не видит Ollama через `localhost`

Ollama в WSL2 с `networkingMode=mirrored` слушает `[::]:11434` (IPv6-wildcard).
Python-библиотеки (`urllib`, `httpx`/`httpcore`) на Windows при резолве `localhost`
получают `::1` первым из DNS и пытаются IPv6-соединение — `ConnectionRefusedError`.
`urllib` делает fallback на `127.0.0.1` и добирается (probe проходит), но `httpcore`
(используемый OpenAI SDK) — нет.

### Рабочий способ запуска e2e-тестов

```powershell
# Запускать через WSL: Windows-exe видит Ollama через WSL-loopback
wsl -d Ubuntu -- .venv/Scripts/python.exe -m pytest tests/test_e2e_llm.py -m llm -s --tb=short
```

### Долгосрочное исправление

Ollama уже имеет `OLLAMA_HOST=0.0.0.0:11434` в `/etc/systemd/system/ollama.service`,
но `ss -tlnp` показывает `[::]:11434` (Linux dual-stack). При `networkingMode=mirrored`
порты WSL зеркалируются на Windows, но для IPv6-first биндинга они не всегда доступны
через `127.0.0.1`. Решение — явный `OLLAMA_HOST=0.0.0.0` (IPv4-only bind):

```bash
# В WSL2 Ubuntu:
sudo sed -i 's/OLLAMA_HOST=0.0.0.0:11434/OLLAMA_HOST=0.0.0.0/' /etc/systemd/system/ollama.service
sudo systemctl daemon-reload && sudo systemctl restart ollama
# Проверить: ss -tlnp | grep 11434 → должно быть 0.0.0.0:11434, не [::]:11434
```

---

## 7. Flash Attention в Ollama

### Проверка

Flash Attention не используется напрямую в коде Python-проекта (`botkin` вызывает Ollama через HTTP API). Оптимизация работает на уровне inference-движка Ollama/llama.cpp.

Для нашей рабочей модели `qwen3-vl:8b-instruct` **Flash Attention включён по умолчанию** в Ollama: архитектура `qwen3vl` присутствует в allowlist Ollama (`gemma3`, `gptoss`, `mistral3`, `qwen3`, `qwen3moe`, `qwen3vl`, `qwen3vlmoe`).

Проверить, что модель загружена на GPU и использует оптимизации:

```bash
# В WSL2 Ubuntu
ollama ps
# Ожидаемый вывод: qwen3-vl:8b-instruct ... 100% GPU
```

Точный признак включения Flash Attention можно увидеть в логах Ollama при старте генерации:

```bash
sudo journalctl -u ollama -f | grep -i flash
# или, если Ollama запущен вручную:
OLLAMA_DEBUG=1 ollama serve 2>&1 | grep -i flash
```

### Принудительное включение/выключение

Если нужно явно управлять оптимизацией, задайте переменную окружения серверу Ollama:

```bash
# Включить (актуально для архитектур, не входящих в allowlist)
export OLLAMA_FLASH_ATTENTION=1
ollama serve

# Выключить
export OLLAMA_FLASH_ATTENTION=0
ollama serve
```

Для systemd-сервиса в WSL2 добавьте в `/etc/systemd/system/ollama.service`:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_FLASH_ATTENTION=1"
```

Затем перезапустите сервис:

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

### Почему не добавляем в Python-код

`OLLAMA_FLASH_ATTENTION` читается только сервером Ollama при старте. Клиент (`botkin`) не может его переключить через HTTP-запрос. Поэтому конфигурация остаётся на уровне окружения сервера, а в проекте фиксируется инструкция по проверке.

---

## 8. Удержание модели в VRAM и производительность

### Диагноз: модель не всегда остаётся в памяти

Код `botkin` уже передаёт `keep_alive: "30m"` в каждом Ollama-запросе через `options` (`src/botkin/llm/client.py`). Однако в некоторых версиях Ollama OpenAI-совместимый endpoint игнорирует `keep_alive` внутри `options` и использует серверный default (5 минут). В результате модель выгружается из VRAM, если между запросами проходит больше 5 минут.

Проверка текущего поведения:

```bash
ollama ps
# Если UNTIL показывает «5 minutes from now» или меньше — keep_alive серверный, не 30m.
```

### Решение: серверный `OLLAMA_KEEP_ALIVE=-1`

Надёжный способ держать модель в VRAM постоянно — задать переменную окружения серверу Ollama. Значение `-1` означает «не выгружать никогда» (до остановки Ollama).

**Важно:** это привязка 7.6 ГБ VRAM. На GPU с 16 ГБ (например, RTX 3080) запас остаётся, но другие приложения не смогут занять эту память.

### Настройка systemd-сервиса в WSL2

Отредактируйте `/etc/systemd/system/ollama.service`, добавив строку в секцию `[Service]`:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

Затем примените изменения:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Проверьте, что модель остаётся загруженной после запроса:

```bash
ollama ps
# Ожидаемый вывод: UNTIL = Forever
```

### Почему GPU всё равно может простаивать

Даже при модели в VRAM GPU не загружен 100% из-за архитектуры пайплайна:

1. **CPU-предобработка**: перед каждым VLM-вызовом PDF рендерится в изображения (pymupdf), делается deskew/CLAHE/unsharp, идёт OCR и парсинг текстового слоя. Это CPU-работа, GPU в это время idle.
2. **Последовательные вызовы**: пайплайн обрабатывает страницы по очереди (`OLLAMA_NUM_PARALLEL=1`). Пока Python ждёт ответ на запрос N, GPU обрабатывает его; затем Python готовит запрос N+1 — и в этот момент GPU простаивает.
3. **Text-only вызовы структурирования**: `_structure_text` и `_call_text` используют ту же модель `qwen3-vl:8b-instruct`. Это vision-модель, и для текстовых задач она менее эффективна, чем специализированная text-only модель. На текстовых задачах GPU-utilization может быть низкой.
4. **Таймауты и ретраи**: если модель «залипает» (например, structured output на сложной картинке), запрос висит до таймаута, а GPU не получает новой работы.

### Что можно улучнить дальше

- **Разделить text-only и VLM модели**: для `_structure_text` (координатные строки → JSON) использовать лёгкий text-only LLM (например, `qwen3:8b` или `huihui_ai/qwen3-abliterated:8b`), освободив vision-модель для картинок. Это потребует отдельного `TEXT_MODEL` конфига и fallback-механики.
- **Параллельная обработка страниц**: если страницы независимы, можно запускать несколько VLM-запросов параллельно (`OLLAMA_NUM_PARALLEL` + `asyncio.gather`), но это увеличивает пиковое потребление VRAM и требует аккуратного дедупа.
- **Профилирование**: запустить `nvidia-smi dmon` и `ollama ps` в отдельных терминалах во время обработки одного документа, чтобы увидеть, на каких этапах GPU падает в 0%.

---

## 9. RAG (справочники + health) и синхронизация Garmin/Strava/Apple Health

### Архитектура

- **RAG** (`src/botkin/rag/`): эмбеддинги — `bge-m3` через Ollama `/api/embed` (1024-dim);
  вектора — sqlite-vec (vec0-таблица `rag_vectors`) в основной `data/botkin.db`, чанки —
  таблица `rag_chunks`. Индексируются: справочник ГРЛС (20 948 лекарств), ФСЛИ (5 924 анализа)
  и недельные сводки health-метрик пациента. Ассистент (`rag/recommend.py`) отвечает моделью
  `qwen3:8b` с контекстом из отклонений анализов, назначенных лекарств, health-агрегатов и
  RAG-выдачи; промпт запрещает назначать лечение.
- **Health-sync** (`src/botkin/health/`): Garmin — неофициальная библиотека `garminconnect`
  (логин по паролю ОДИН раз при подключении, дальше OAuth-токены в
  `data/health_tokens/<user_id>/garmin/`, вне git); Apple Health — импорт `export.zip`
  (потоковый iterparse) и приём JSON от Health Auto Export; Strava — OAuth за конфигом
  `STRAVA_CLIENT_ID/SECRET` (отдаёт только тренировки). Хранение: `health_accounts`,
  `health_metrics` (идемпотентный upsert по user+provider+metric+taken_at),
  `health_activities` (идемпотентно по external_id).

### Важно про Garmin rate limit

SSO-логин Garmin агрессивно лимитируется (429 → блок аккаунта на ~48 ч, привязан к email).
Поэтому НЕ логиниться повторно по паролю: сессия восстанавливается из токенов
(`garmin.resume`), refresh живёт ~30 дней и ротируется при каждом синке. Между запросами
данных выдерживается пауза `HEALTH_REQUEST_PAUSE` (0.5 с).

### Первичная настройка и проверка

`powershell
# Эмбеддер (однократно, в WSL2)
wsl -d Ubuntu -- ollama pull bge-m3

# Живой прогон по шагам (см. docstring скрипта)
uv run python scripts/live_check_rag_health.py index    # индексация справочников (~8 мин на RTX 3080)
uv run python scripts/live_check_rag_health.py sync     # Garmin за 30 дней (~2 мин, ~21k метрик)
uv run python scripts/live_check_rag_health.py search   # смоук семантического поиска
uv run python scripts/live_check_rag_health.py ask      # рекомендация LLM
`

Через кабинет: экран «Здоровье» — подключение Garmin (email/пароль), синк с прогрессом,
импорт Apple export.zip, графики метрик, тренировки и ассистент. API: `/api/health/*`,
`/api/rag/*` (см. `src/botkin/api/routes/health_sync.py` и `rag.py`).

### Ловушки, уже наступленные

- `.gitignore`-паттерн `_*.py` без якоря матчил `src/**/__init__.py` — заякорен (`/_*.py`).
- `node -e` со всем app.js в аргументе упирается в лимит командной строки Windows
  (32 767 симв., WinError 206) — тесты пишут скрипт во временный файл.
- PowerShell глотает кириллицу UTF-8-файлов: ad-hoc проверки делать через
  `uv run python` с `PYTHONIOENCODING=utf-8`.

Подробная инструкция по Garmin (авторизация, токены, rate limits, troubleshooting,
оценка безопасности библиотек garminconnect/curl_cffi) — `docs/garmin-integration-guide.md`.
