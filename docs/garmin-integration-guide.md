# Garmin Connect в botkin: инструкция и оценка безопасности

> Актуально на 2026-07-04. Стек: `garminconnect` 0.3.6 + `curl_cffi` 0.15.0,
> модуль `src/botkin/health/garmin.py`, токены в `data/health_tokens/<user_id>/garmin/`.

---

## 1. Почему неофициальная библиотека

У Garmin **нет официального API для физлиц**:

- Garmin Health API / Wellness API выдаётся только юрлицам по партнёрскому соглашению;
  в 2026 форма заявки удалена, приём новых партнёров приостановлен без даты возобновления
  ([forums.garmin.com](https://forums.garmin.com/developer/connect-iq/f/discussion/434542/)).
- Официальные альтернативы: privacy-экспорт архива аккаунта (ZIP, 24–48 ч, вручную)
  и CSV/TCX-экспорт по одной активности из веб-кабинета. Ни то ни другое не даёт
  автоматической ежедневной синхронизации.

Поэтому используется `garminconnect` (cyberjunky/python-garminconnect) — обёртка над теми
же внутренними REST-эндпоинтами, которыми пользуются приложение и сайт Garmin Connect.

## 2. Как устроена авторизация

### 2.1. Схема

1. **Первичный логин** (`garmin.connect(user_id, email, password)`): библиотека проходит
   DI OAuth (Digital Identity) флоу Garmin. С марта 2026 Garmin закрыл часть SSO-эндпоинтов
   Cloudflare'ом (TLS fingerprinting, JA3) — поэтому нужен `curl_cffi`, имитирующий TLS-стек
   Chrome. Библиотека перебирает стратегии: `portal+cffi` → `portal+requests` →
   `mobile+cffi` → `mobile+requests` → **widget login** (HTML-форма `/sso/embed` + CSRF).
   На нашем стенде mobile-стратегии получили 429, widget-фоллбек прошёл.
2. **Токены сохраняются** в `data/health_tokens/<user_id>/garmin/garmin_tokens.json`.
   Пароль после этого **не нужен и нигде не хранится**.
3. **Все последующие синки** — `garmin.resume(user_id)`: сессия поднимается из токенов,
   протухший access-токен обновляется по refresh-токену без похода в SSO.

### 2.2. Что лежит в garmin_tokens.json (разбор живого токена)

```json
{ "di_token": "<JWT ~1.8 КБ>", "di_refresh_token": "<...>", "di_client_id": "<...>" }
```

- `di_token` — JWT (RS256), **живёт ~20.5 часа** (`iat` → `exp`).
- `di_refresh_token` — ротируется при каждом обновлении; при регулярном использовании
  сессия продлевается неограниченно, при простое умирает через ~30 дней.
- **Скоупы токена — почти полный доступ к аккаунту** (проверено декодированием payload):
  `CONNECT_READ`, `CONNECT_WRITE`, `GARMINPAY_READ/WRITE`, `DI_COACHING_WRITE`,
  `GOLF_API_WRITE`, `GCOFFER_READ/WRITE`, `INSIGHTS_WRITE` и ещё ~25. Garmin не выдаёт
  «токен только на чтение» — файл токенов нужно защищать как пароль (см. раздел 6).

### 2.3. Rate limit: главное правило

SSO-логин по паролю жёстко лимитируется: **429 → блок аккаунта на 48+ часов**, причём
блок привязан к email, а не к IP ([issue #344](https://github.com/cyberjunky/python-garminconnect/issues/344),
[форум Garmin](https://forums.garmin.com/developer/fit-sdk/f/discussion/435087/)).

Правила, зашитые в наш коннектор:

- логин по паролю — ровно один раз, при подключении в кабинете;
- дальше только `resume()` из токенов;
- пауза `HEALTH_REQUEST_PAUSE` (0.5 с) между запросами данных;
- сбой одного дня не роняет синк (день пропускается с warning).

Чего делать **нельзя**: ретраить упавший логин в цикле; запускать синк по крону чаще
нескольких раз в сутки; логиниться параллельно с нескольких машин под одним email.

## 3. Подключение и синхронизация (пользовательский путь)

### 3.1. Через веб-кабинет

1. Запустить API: `uv run uvicorn botkin.api.app:app --host 0.0.0.0 --port 8000`.
2. Экран «Здоровье» → блок Garmin Connect → ввести email/пароль → «Подключить Garmin».
3. Кнопка «Синхронизировать (30 дней)» — фоновая задача с прогрессом
   (`GET /api/health/sync/status`). Повторные синки идемпотентны:
   UNIQUE(user, provider, metric, taken_at) в `health_metrics`.

### 3.2. Из консоли (оператор)

```powershell
# .env: GARMIN_EMAIL / GARMIN_PASSWORD (только для первичного подключения)
uv run python scripts/live_check_rag_health.py sync     # синк за 30 дней + RAG-сводки
uv run python scripts/live_check_rag_health.py search   # смоук поиска
```

### 3.3. API

| Метод | Что делает |
|---|---|
| `POST /api/health/connect/garmin` `{email, password}` | одноразовый логин, сохранение токенов |
| `POST /api/health/sync/garmin?days=30` | фоновый синк (метрики + активности + RAG-сводки) |
| `GET /api/health/sync/status` | прогресс `{state, done, total}` |
| `GET /api/health/metrics` / `series?metric=` / `activities` | чтение данных |
| `DELETE /api/health/accounts/garmin` | отключение источника |

## 4. Какие данные забираем и откуда

| Наша метрика | Метод garminconnect | Гранулярность | Примечание |
|---|---|---|---|
| `heart_rate` | `get_heart_rates(date)` → `heartRateValues` | ~кажд. 2 мин (до 720 точек/день) | |
| `resting_heart_rate` | `get_heart_rates(date)` → `restingHeartRate` | 1/день | |
| `steps` | `get_steps_data(date)` (сумма интервалов) | 1/день | |
| `sleep_seconds` | `get_sleep_data(date)` → `dailySleepDTO` | 1/ночь | фазы (deep/light/REM/awake) в `value_json` |
| `stress_avg` | `get_all_day_stress(date)` | 1/день | |
| `body_battery_max` | `get_all_day_stress(date)` → `bodyBatteryValuesArray` | 1/день | |
| `hrv_last_night` | `get_hrv_data(date)` → `hrvSummary.lastNightAvg` | 1/ночь | |
| `blood_pressure_*` + `bp_pulse` | `get_blood_pressure(from, to)` | по замерам | тонометр Garmin Index BPM или ручной ввод; без замеров — пустой список |
| `weight_kg` | `get_weigh_ins(from, to)` | по взвешиваниям | граммы → кг |
| активности | `get_activities_by_date(from, to)` | по тренировкам | тип, длительность, дистанция, пульс, калории, raw_json |

Живой замер (аккаунт с Forerunner, 30 дней): 21 013 метрик + 16 активностей за 113 с.

Ещё доступно в библиотеке (не забираем — добавлять по потребности): SpO2
(`get_spo2_data`), дыхание (`get_respiration_data`), интенсивные минуты, VO2max /
training status (`get_training_status`), состав тела (`get_body_composition`),
скачивание FIT/TCX/GPX (`download_activity`), всего 130+ методов.

## 5. Устранение неполадок

| Симптом | Причина | Действие |
|---|---|---|
| 429 при подключении | rate limit SSO (email-scoped) | ждать 24–48 ч; НЕ ретраить; проверить, что нет других скриптов под этим email |
| `Cloudflare Error 1015` | бан на уровне Cloudflare | не использовать VPN; ждать ~24 ч |
| `resume` падает, синк требует переподключения | refresh-токен протух (>30 дней простоя) или инвалидацию вызвала смена пароля | переподключить через кабинет |
| MFA-запрос при логине | на аккаунте включена 2FA | `Garmin(email, password, prompt_mfa=...)`; в веб-флоу пока не поддержано — подключать из консоли |
| Пустое давление/вес | у аккаунта нет тонометра/весов Garmin | норма, коннектор терпит пустые ответы |
| `mobile+cffi returned 429` в логах при успешном логине | часть стратегий отбита, widget-фоллбек сработал | ничего не делать, это штатно |

## 6. Оценка безопасности сторонних библиотек

### 6.1. Сводка по компонентам

| Компонент | Версия у нас | Зрелость | Известные проблемы | Статус |
|---|---|---|---|---|
| `garminconnect` | 0.3.6 | ~3000★, 581 коммит, активные релизы, один мейнтейнер (Ron Klinkien) | **GHSA-wjhr-76vg-2hvc**: до 0.3.5 файл токенов создавался world-readable (0o644) | исправлено в 0.3.5; у нас 0.3.6 ✅ |
| `curl_cffi` | 0.15.0 | активная поддержка, быстрые фиксы | **CVE-2023-38545** (heap overflow SOCKS5, fixed ≥0.7.0); **CVE-2026-33752** (SSRF через редиректы, CVSS 8.6, fixed =0.15.0) | обе закрыты в 0.15.0 ✅ |
| `garth` (matin) | не используем | 811★ | DEPRECATED: Garmin сломал mobile-auth, новые логины не работают | наш `garminconnect` 0.3.x от garth уже не зависит |

Typosquatting-атак именно на эти имена на PyPI не зафиксировано (общие кампании на PyPI
существуют — ставить только точные имена пакетов, версии пиновать в `uv.lock`).

### 6.2. Модель угроз для файла токенов

Утечка `garmin_tokens.json` = утечка аккаунта: refresh-токен обменивается на свежие
access-токены до отзыва, а скоуп включает **запись** (`CONNECT_WRITE`, `GARMINPAY_WRITE`,
coaching, golf). То есть злоумышленник сможет читать всю историю здоровья, менять данные
и управлять устройствами. Отзыв — сменой пароля аккаунта Garmin (инвалидирует refresh).

Что сделано у нас:

- пароль не персистится вовсе (живёт один HTTP-запрос подключения);
- токены — вне git (`data/health_tokens/` в .gitignore), по каталогу на пользователя;
- garminconnect ≥0.3.5 сам ставит права 0o700/0o600 (актуально для Linux-деплоя;
  на Windows ACL наследуются — каталог проекта не должен быть общим).

### 6.3. Юридические риски и риск блокировки

- **ToS Garmin запрещают** reverse engineering и автоматизированный доступ вне
  официального API ([terms-of-use](https://www.garmin.com/en-US/legal/terms-of-use/)).
  Использование — на свой риск; для коммерческого продукта нужен официальный
  партнёрский Health API (сейчас закрыт для новых заявок).
- **Временные блокировки** (429/403/1015 на 24–72 ч) — частое и подтверждённое явление.
- **Деактивация аккаунта** — зафиксирован один подтверждённый случай
  ([gcexport #60](https://github.com/pe-st/garmin-connect-export/issues/60)): пользователь
  синхронизировался каждый час; поддержка Garmin прямо попросила отключить скрипт.
  Риск низкий, но ненулевой.

### 6.4. Практики снижения риска (применяем / рекомендуем)

1. **Один логин, дальше токены** — реализовано в коннекторе.
2. **Редкие синки**: 1–2 раза в сутки достаточно (данные дневные); не по крону каждый час.
3. **Паузы между запросами** — `HEALTH_REQUEST_PAUSE=0.5` с (настраивается).
4. **Отдельный аккаунт Garmin** для экспериментов, если основной терять нельзя.
5. **Обновлять зависимости**: `garminconnect` и `curl_cffi` чинят и уязвимости, и
   поломки от изменений на стороне Garmin; следить за релизами и issue-трекером.
6. **MFA на аккаунте** — библиотека поддерживает (`prompt_mfa`), безопасность аккаунта выше.
7. **План Б**: официальный privacy-экспорт (Account → Data Management → Export Your Data) —
   легальный полный слепок данных; годится как холодный бэкап и независим от блокировок.

### 6.5. Итоговая матрица выбора

| Способ | Автоматизация | Риск ToS/блокировки | Полнота данных | Вердикт для botkin |
|---|---|---|---|---|
| `garminconnect` (неофиц.) | полная | средний (429 часты, бан маловероятен) | вся дневная телеметрия | **основной путь** (локальный research-проект) |
| Privacy-экспорт архива | нет (вручную, 24–48 ч) | нулевой | полная, но снапшотом | резерв/бэкап |
| Garmin Health API | полная (push) | нулевой | активность+wellness | недоступен физлицам, заявки закрыты |
| CSV/TCX из веб-кабинета | нет | нулевой | по одной активности | непригодно |

## 7. Ссылки

- python-garminconnect — https://github.com/cyberjunky/python-garminconnect
- Advisory о правах на токен-файл — https://github.com/cyberjunky/python-garminconnect/security/advisories/GHSA-wjhr-76vg-2hvc
- Rate limit 429 (issue #344) — https://github.com/cyberjunky/python-garminconnect/issues/344
- Блок аккаунта на 48 ч (форум Garmin) — https://forums.garmin.com/developer/fit-sdk/f/discussion/435087/
- Cloudflare-блок SSO (garth #217) — https://github.com/matin/garth/issues/217
- Деактивация аккаунта (gcexport #60) — https://github.com/pe-st/garmin-connect-export/issues/60
- CVE-2026-33752 (curl_cffi SSRF) — https://nvd.nist.gov/vuln/detail/CVE-2026-33752
- CVE-2023-38545 (curl SOCKS5) — https://curl.se/docs/CVE-2023-38545.html
- Garmin Terms of Use — https://www.garmin.com/en-US/legal/terms-of-use/
- Garmin Health API — https://developer.garmin.com/gc-developer-program/health-api/
