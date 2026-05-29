# Drug Instruction Extractor

Инструмент для извлечения структурированных полей из сырого текста инструкций лекарственных препаратов,
хранящихся в таблице `drug_instructions` базы данных `botkin.db`.

## Проблема

Таблица `drug_instructions` содержит поле `raw_text` — полный текст страницы, спаршенной с разных медицинских сайтов.
В тексте перемешаны навигация сайта, рекламные блоки, отзывы пользователей и сама инструкция.

Цель: извлечь из `raw_text` только полезные поля и разложить их по колонкам в нормализованной таблице.

## Результат

После запуска создаётся таблица `drug_parsed_instructions` с 44 именованными полями (+ метаданные).

### Схема целевой таблицы

| Колонка | Тип | Описание |
|---|---|---|
| `id` | INTEGER PK | Автоинкремент |
| `source_id` | INTEGER FK | → `drug_instructions.id` |
| `reg_number` | TEXT | Регистрационный номер |
| `trade_name` | TEXT | Торговое наименование |
| `mnn` | TEXT | МНН (международное непатентованное) |
| `synonyms` | TEXT | Синонимы / торговые аналоги |
| `dosage_form` | TEXT | Лекарственная форма |
| `release_form_and_packaging` | TEXT | Форма выпуска и упаковка |
| `packaging` | TEXT | Упаковка |
| `manufacturer` | TEXT | Производитель |
| `atx_code` | TEXT | Код АТХ |
| `pharmacological_group` | TEXT | Фармакологическая / терапевтическая группа |
| `pharmacological_properties` | TEXT | Фармакологические свойства (сводный блок) |
| `pharmacological_action` | TEXT | Фармакологическое действие |
| `pharmacodynamics` | TEXT | Фармакодинамика |
| `mechanism_of_action` | TEXT | Механизм действия |
| `pharmacokinetics` | TEXT | Фармакокинетика |
| `composition` | TEXT | Состав |
| `excipients` | TEXT | Вспомогательные вещества |
| `description` | TEXT | Описание лекарственной формы |
| `indications` | TEXT | Показания к применению |
| `contraindications` | TEXT | Противопоказания |
| `contraindications_absolute` | TEXT | Абсолютные противопоказания |
| `use_with_caution` | TEXT | С осторожностью |
| `dosage_and_administration` | TEXT | Способ применения и дозы |
| `side_effects` | TEXT | Побочные эффекты |
| `interactions` | TEXT | Лекарственные взаимодействия |
| `overdose` | TEXT | Передозировка (полный блок) |
| `overdose_symptoms` | TEXT | Передозировка — симптомы |
| `overdose_treatment` | TEXT | Передозировка — лечение |
| `special_instructions` | TEXT | Особые указания |
| `notes` | TEXT | Примечания |
| `driving_ability` | TEXT | Влияние на вождение |
| `pregnancy_and_lactation` | TEXT | Беременность и лактация |
| `pregnancy_category` | TEXT | Категория риска при беременности |
| `pediatric_use` | TEXT | Применение у детей |
| `geriatric_use` | TEXT | Применение у пожилых |
| `renal_impairment` | TEXT | При нарушении функции почек |
| `hepatic_impairment` | TEXT | При нарушении функции печени |
| `storage_conditions` | TEXT | Условия хранения |
| `shelf_life` | TEXT | Срок годности |
| `dispensing_conditions` | TEXT | Условия отпуска |
| `coating_composition` | TEXT | Состав оболочки |
| `analogs` | TEXT | Аналоги |
| `parse_method` | TEXT | Метод парсинга: `regex` / `llm` |
| `filled_fields_count` | INTEGER | Число заполненных содержательных полей |
| `extra_fields_json` | TEXT | JSON с полями, не вошедшими в схему |

## Установка

Зависимости управляются через `uv` (менеджер проекта). Скрипт использует только
библиотеки уже присутствующие в проекте (`typer`, `rich`, стандартная библиотека Python).

Для LLM fallback дополнительно нужен пакет `ollama`:

```bash
uv add ollama
```

## Команды

### Полный прогон (все записи)

```bash
uv run parsing/drug_instruction_extractor.py run
```

### Прогон с ограничением

```bash
uv run parsing/drug_instruction_extractor.py run --limit 100
```

### Dry-run (без записи в БД)

```bash
uv run parsing/drug_instruction_extractor.py run --dry-run
```

### С LLM fallback для записей с < 4 полями

```bash
uv run parsing/drug_instruction_extractor.py run --llm-fallback --llm-model huihui_ai/qwen3-abliterated:14b
```

> Требует работающего ollama (`ollama serve`) и установленного пакета `ollama`.
> Прогон 1500+ записей с LLM займёт ~2–4 часа.

### Просмотр результата для одной записи

```bash
uv run parsing/drug_instruction_extractor.py inspect 16
```

Выводит таблицу со всеми извлечёнными полями без записи в БД.

### Кастомный путь к БД

```bash
uv run parsing/drug_instruction_extractor.py run --db /path/to/other.db
```

## Как работает алгоритм

### 1. Очистка текста от «шума»

Функция `_strip_noise_header` убирает строки навигации сайта (регулярные выражения
в `NOISE_PATTERNS`) и обрезает хвост со строки-маркера («Отзывы», «© 2026», «Добавить отзыв»).

### 2. Поиск заголовков секций

Каждая строка текста проверяется:
- если её нормализованная версия (lowercase, убраны «:» и пробелы) есть в `SYNONYM_MAP` →
  это известный заголовок, маппится на каноническое имя поля;
- если строка заканчивается на «:» но не в словаре → неизвестный заголовок,
  сохраняется в `extra_fields_json`.

### 3. Извлечение значений

Текст между двумя соседними заголовками — значение первого. Строки объединяются через пробел.

### 4. Специальная обработка «Передозировки»

Блок «Передозировка» часто содержит внутри себя подразделы «Симптомы:» и «Лечение:»
без отдельных заголовков. Regex внутри блока разбирает их на `overdose_symptoms` и `overdose_treatment`.

### 5. Валидация значений

Функция `_clean_value` фильтрует:
- заглушки: `~`, `нет данных`, `—`, `-`, пустые строки;
- UI-шум сайтов: «Показать ещё», «Данные из справочника ЕСКЛП», «Оформить подписку» и т.п.

### 6. Приоритет данных

Для `trade_name`, `mnn`, `reg_number`:
1. Значение из секции инструкции (самое точное).
2. Значение из поля `drug_instructions` в БД.
3. Эвристика по первой строке текста (только для `trade_name`).

## Метрики на полном датасете (1573 записи)

| Метрика | Значение |
|---|---|
| Всего обработано | 1573 |
| Записей с ≥ 4 полями | 1231 (78.3%) |
| Среднее число полей | 7.5 |
| Максимум полей | 12 |
| Ошибок парсинга | 0 |
| trade_name заполнен | 100% |
| mnn заполнен | 97% |
| indications заполнены | ~71% |
| contraindications заполнены | ~70% |

Записи с < 4 полями (22%) — это, как правило, не фармацевтические инструкции:
страницы интернет-магазинов, статьи народной медицины, описания ингредиентов косметики.

## Добавление новых синонимов заголовков

Если в `extra_fields_json` часто встречается один и тот же ключ — добавьте его в `SYNONYM_MAP`:

```python
# В parsing/drug_instruction_extractor.py
SYNONYM_MAP: dict[str, str] = {
    ...
    "новый заголовок сайта": "canonical_field_name",
    ...
}
```

Затем перезапустите прогон с флагом `--limit 0` — таблица обновится через `ON CONFLICT... DO UPDATE`.

## Структура кода

```
parsing/drug_instruction_extractor.py
├── SYNONYM_MAP          — словарь синонимов заголовков → каноническое поле
├── MEANINGFUL_FIELDS    — поля для оценки качества парсинга
├── NOISE_PATTERNS       — regex для навигации/UI сайтов
├── TAIL_SENTINELS       — маркеры конца полезного контента
├── ParsedInstruction    — dataclass со всеми полями результата
├── _normalize_key()     — нормализация заголовка
├── _clean_value()       — валидация и очистка значения
├── _strip_noise_header()— удаление шума из текста
├── _extract_name_from_header() — эвристика имени из начала текста
├── _parse_with_regex()  — основной парсер
├── _parse_with_llm()    — LLM fallback через ollama
├── _ensure_table()      — DDL для drug_parsed_instructions
└── app                  — CLI (typer): команды run, inspect
```
