# Независимое код-ревью Botkin — 2026-06-22

> **Статус выполнения (2026-06-23):** дорожная карта пройдена полностью.
> P0-1…P0-4, P1-1…P1-5 и весь раздел P2 закрыты и закоммичены (ветка
> `refactor/architecture-cleanup`). Прогресс и решения по каждому пункту — в
> `habr/lab-results-journal.md` (итерации 16–20). Единственный сознательный
> пропуск — `process.cdist` для батч-нормализации (преждевременная оптимизация:
> панели документа — десятки строк, exact-hit уже закрывает частый случай за O(1)).

Ревью охватывает ~3760 LOC `src/botkin/`. Проведено четырьмя независимыми ревьюерами
(LLM-подсистема; pipeline/api/bot/db; normalize/preprocess/config; внешние бестпрактис),
ключевые находки перепроверены по первоисточнику. Ветка `feature/lab-results-recognition`,
HEAD `b761e8d`.

## Общая оценка

Код **функционально зрелый**: продуманная устойчивость (многоуровневые fallback VLM↔текстовый
слой, два стража против галлюцинаций/пропусков), отличные type hints, честные «почему»-докстринги,
чистый доменный слой, тенант-изоляция `WHERE user_id = ?` во всех запросах. Это сильные стороны —
их надо сохранить.

**Главный технический долг — архитектурный, не функциональный.** Три сквозные темы:

1. **Смешение слоёв.** Чистое доменное ядро парсинга (~400 строк) живёт в пакете `llm/`;
   доступ к данным размазан по трём механизмам (`repos` / `queries` / инлайн-SQL в оркестраторе);
   нормализация вшита в persist.
2. **Дублирование.** Два нормализатора — структурные близнецы с байт-в-байт копипастой; два
   read/write пути к БД дублируют запросы; копипаст хендлеров бота; тройной дефолт в config.
3. **Тестируемость VLM-пути.** Клиент создаётся внутри функций, опора на приватные атрибуты
   instructor, модульные синглтоны как глобальное состояние.

---

## Приоритизированная дорожная карта рефакторинга

### P0 — высокий эффект, делать первыми

**P0-1. Выделить чистое доменное ядро парсинга из `llm/extract.py` (772 строки).**
~400 строк — это домен без LLM: `parse_lab_value`, `parse_reference_range`, harvester
(`_harvest_row`, `harvest_lab_rows`, `_salvage_json_objects`), парсер текстового слоя
(`_parse_text_line`, `completeness_guard`, `_verbatim_guard`), dedup. Перенести в
`botkin/parsing/{scalars,harvester,text_layer}.py`. `extract.py` оставить только
оркестрацию (`run_analysis`, `run_doctor_report`, `_call_vlm`, `_extract_once`,
`_structure_text`).
*Зачем:* режет главный файл вдвое, снимает импорт instructor для чистой логики, изолирует
тесты. Решает SRP-нарушение и большую часть проблем тестируемости.
*Бестпрактис:* «не пускай Pydantic/LLM в доменный слой» — clean architecture
(domain/application/infrastructure). Доменные сущности — чистые dataclass, Pydantic только
на границе.

**P0-2. Единый слой доступа к данным.**
Слить `db.queries` + инлайн-SQL оркестратора (`_persist_lab`, `_persist_doctor_report`,
`_save_raw_extraction`, `_mark_failed`) в репозитории. Устранить дубли `get_document`
(`queries.py:43` ↔ `repos.py:72`) и `get_user_id` (`queries.py:23` ↔ `UserRepo`).
Заменить 23-позиционный безымянный INSERT (`orchestrator.py:207-222`) на именованные
параметры. Мёртвый `DocumentRepo.mark_failed` (`repos.py:69`) — подключить или удалить.
*Зачем:* убирает три параллельных контракта доступа к данным, главный источник рассинхрона.

**P0-3. Транзакции и коннект к БД.**
`get_conn()` открывает новый `sqlite3.connect` на каждый вызов (6+ на документ в `_run`);
`isolation_level=None` (autocommit) при рассыпанных `conn.commit()`. Вставка панели в
`_persist_lab` **не атомарна** вопреки замыслу — при падении на середине панель сохранится
частично. Ввести явные транзакции (`with conn:`), один коннект на обработку документа.
*Зачем:* реальная атомарность, которую код обещает, но не даёт; меньше риска `database is locked`.

**P0-4. Выделить `BaseNormalizer` / общий `Registry`.**
`analytes.py` и `drugs.py` дублируют байт-в-байт: `_read_registry`
(`analytes.py:203-215` ↔ `drugs.py:127-139`), `_normalize_name`, `_unverified`, ядро
`correct()` (расчёт `cap`, `extractOne`, ratio-floor постфильтр). Поднять общую часть в
базовый класс; analytes переопределяет хук коротких ключей. Закэшировать загрузку реестра
`@lru_cache` в самом модуле (сейчас кэш живёт вне — в синглтонах оркестратора).
*Зачем:* правка алгоритма матчинга сейчас требует синхронной правки двух файлов.

### P1 — заметный эффект

**P1-1. Версионирование и консолидация промптов.**
Все промпты — хардкод-константы в `prompts.py` без версий и few-shot; часть user-инструкций
раскидана по `extract.py:428,459,771` и `classify.py:36`. Свести все инструкции в `prompts.py`;
дать каждому промпту версию и логировать её в `[SUCCESS_EXTRACT]`; добавить few-shot блок
(пара «строка бланка → JSON») для стабилизации недетерминированного qwen3-vl.
*Бестпрактис:* единица истины — «Prompt Asset» (текст + model ID + temperature + целевая
схема как один версионированный артефакт). Вынос в Jinja2-шаблоны (`prompts/extract_lab.j2`),
библиотека `dynaprompt` (Jinja2+Pydantic) близка по стеку.
*Доклад:* без версии промпта в логах нельзя отличить регрессию промпта от регрессии модели
при апгрейде Ollama — критично для воспроизводимости.

**P1-2. Нативный structured output Ollama вместо prompt-only.**
Ollama с дек.2024 форсит JSON-схему на уровне декодирования (XGrammar → 100% соответствие).
Передавать `pydantic.model_json_schema()` в параметр `format`. Тогда ретраи нужны только на
семантические провалы (показатель есть в PDF, но не извлечён — анти-пропускной страж), а не
на парсинг мусора. Это усиливает harvester/salvage, частично делая их ненужными.

**P1-3. Декомпозиция оркестратора + реестр обработчиков `doc_type`.**
`_run` (~100 строк) совмещает чтение БД, статусы, LLM, бизнес-логику заголовка (124-138),
persist и доставку. Ветвление `doc_type` — жёсткий `if/elif` (`orchestrator.py:117/140/147`),
добавление типа = правка ядра (OCP). Выделить стадии classify/extract/normalize/persist/deliver;
заменить ветвление на `dict[doc_type] -> handler`. Нормализацию вынести из `_persist_lab`
в отдельную стадию.

**P1-4. Синхронизировать `DocStatus` со схемой; убрать утечку ошибок пользователю.**
`DocStatus = Literal["received","processing","extracted","failed"]` не содержит реальных
`"recognizing"`/`"normalizing"` (orchestrator.py:89,112), а `"processing"` не выставляется
нигде. Сделать статусы единым источником правды, переиспользовать в `cards.STATUS_EMOJI`.
`notifications.*_failed` шлют `str(e)` в Telegram — утечка внутренней диагностики в мед.продукте;
наружу давать обобщённый текст, детали — в лог.

**P1-5. Устойчивость LLM-вызовов: tenacity + обёртка приватного API instructor.**
`response._raw_response.usage` (`extract.py:343`, `classify.py:53`) — приватный атрибут
instructor **без try**: успешный вызов упадёт, если usage недоступен. `max_retries=2` захардкожен,
в `classify` ретраев нет вовсе. Ввести `tenacity` (exponential backoff + jitter, stop по числу
И по времени), ретраить на провале pydantic-валидации, не на 4xx-контенте. Обернуть доступ к
`usage`/`_raw_response` в try.

### P2 — гигиена, низкий риск

- **config.py:** унифицировать env-override (сейчас VLM_* читают env, а `IMAGE_*`/`DRUG_*`/
  `ANALYTE_*`/DPI/`MAX_PAGES` — нет; 12-factor дыра). Либо `pydantic-settings` (типизация +
  валидация + env бесплатно), либо единый хелпер `setting(path, env_name)`. Убрать тройное
  дублирование дефолта `_get(..., _DEFAULTS[...])`.
- **Дедуп бота:** общий блок `on_photo`/`on_document` (`handlers/upload.py:118-130`≡`154-165`);
  объединить `render_document_card`/`_render_card`; маркер нормы ⬆️/⬇️ реализован трижды
  (`show._ref_marker`, `cards.format_labs_summary`, `_format_ref`). Разорвать цикл импорта
  `from ...show import _format_document` внутри функций.
- **Дедуп fallback-конвейера** в `extract.py`: `_extract_once` (`:444-456`) и `_structure_text`
  (`:484-488`) — почти идентичный «raw→rows_from_raw→harvest→salvage». Один хелпер `_rows_or_harvest`.
- **Магические числа:** `max_retries=2`, text-`temperature=0.0` (`extract.py:471`), лимит лога
  `4000`, таймауты `1.5`/`5` (`client.py`); CLAHE `tileGridSize=(8,8)`, сигма unsharp `3`,
  морфо-ядра `(9,9)`/`(35,35)` (`images.py`), `_SHORT_KEY_LEN=3` (`analytes.py:24`) → в config.
- **pdf_text реконструкция строк:** распаковать кортеж `(x0,y0,x1,y1,word,*_)` вместо `w[0]/w[1]/w[4]`;
  кластеризация по скользящему центроиду, а не по анкеру первого слова (плавный дрейф строки рвёт её).
- **DrugNormalizer:** exact-hit `self._by_key.get(query)` до `extractOne` (минует скан 21K).
  Для батч-матчинга — `process.cdist(workers=-1)` вместо цикла `extractOne`.
- **Прочее:** широкий `except Exception` (`client.py:35,61` глушат в `pass`); `notify_user`
  создаёт новый `Bot` на каждое уведомление; `asyncio.get_event_loop()` (устарел) →
  `asyncio.to_thread`; `_pdf_pages` → `with pymupdf.open(...)`; `source_text`-параметр затеняет
  импорт-функцию (`extract.py:520`).

---

## Эталонные репозитории (изучить как образец)

| Репо | Чему учиться |
|------|--------------|
| **katanaml/sparrow** | Ближайший аналог по стеку: извлечение структурированных данных через локальный Ollama/Vision-LLM, API-first, JSON. Образец разделения VLM-извлечения и оркестрации. |
| **docling-project/docling** (IBM, MIT) | Многостадийный парсинг PDF (layout→OCR→table→export), таблица как структурный объект. Кандидат в fallback-парсер таблиц рядом с pymupdf. |
| **KatherLab/LLMAIx** | Единственный найденный про **медицинское** извлечение через локальные LLM + privacy. Доменно и приватностно релевантен. |
| **datalab-to/surya** | Мультиязычный OCR (кириллица), кандидат для PDF без текстового слоя (болевая точка `······` из Helvetica). |
| **zhanymkanov/fastapi-best-practices** | Доменная структура (Netflix Dispatch): группировка по доменам, не по типам файлов. |

**Ниша Botkin уникальна:** open-source проекта нормализации рус. лаб-показателей по реестрам
ФСЛИ/ГРЛС не найдено (FHIR-маппинг есть, ФСЛИ — нет). Хороший тезис для доклада: нет прямого
конкурента-образца. Опционально — FHIR R4 как целевая схема для интероперабельности
(StanfordBDHG/LLMonFHIR).

### Источники бестпрактис
- Промпты как версионируемый артефакт: tianpan.co/blog/2026-04-09-prompt-versioning-production-llm;
  Jinja2-управление: dynaprompt (github.com/mohamed-em2m/dynaprompt)
- Structured output: instructor (github.com/567-labs/instructor); Ollama native
  (ollama.com/blog/structured-outputs)
- Ретраи: tenacity.readthedocs.io; python.useinstructor.com/concepts/retrying
- Структура проекта: github.com/zhanymkanov/fastapi-best-practices;
  coderik.nl/posts/keep-pydantic-out-of-your-domain-layer
- rapidfuzz: cdist для батча, processor= для предобработки (кир/лат) — rapidfuzz.github.io/RapidFuzz

---

## Что НЕ трогать (сильные стороны)

- Чистый доменный слой `domain/models.py` (Pydantic-контракты с валидаторами, `extra="forbid"`).
- `progress.poll_until_done` — образцовая инъекция зависимостей (`get_status/edit/sleep/now`).
- Чистые рендеры `cards.py`, чистые функции `numbers/dates/units/formats`.
- Многоуровневый fallback и два стража (verbatim/completeness) — ядро надёжности.
- Содержательные «почему»-докстринги (инцидент D3, кража ключа «тромбоциты», дрейф строки 1px).
- Производные таймауты формулой (`BOT_PROGRESS_TIMEOUT = 30 + 3*VLM_REQUEST_TIMEOUT`).
