"""
Экстрактор структурированных полей из raw_text инструкций лекарственных препаратов.

Логика работы:
1. Regex-парсинг с нормализацией заголовков через словарь синонимов (SYNONYM_MAP).
   Покрывает ~85–90% записей (форматы medi.ru, medvizor.com, vidal.ru, u-doktora.ru и др.)
2. LLM fallback через ollama (huihui_ai/qwen3-abliterated:14b) для записей с < MIN_FIELDS полей.
   Запускается только если ollama доступен.
3. Результат записывается в таблицу drug_parsed_instructions.

Запуск:
    uv run parsing/drug_instruction_extractor.py [--dry-run] [--limit N] [--llm-fallback]

Подробная документация: parsing/README_extractor.md
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

DB_PATH = Path(__file__).parent.parent / "data" / "botkin.db"
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"

# Минимальное количество заполненных содержательных полей для «хорошей» записи.
# Записи ниже порога отправляются на LLM fallback.
MIN_FIELDS_THRESHOLD = 4

# ──────────────────────────────────────────────────────────────
# Словарь синонимов заголовков → каноническое имя поля
# Ключи — строки в нижнем регистре (после strip + lower).
# ──────────────────────────────────────────────────────────────
SYNONYM_MAP: dict[str, str] = {
    # Идентификация препарата
    "регистрационный номер": "reg_number",
    "регистрационный номер препарата": "reg_number",
    "номер регистрационного удостоверения": "reg_number",
    "торговое наименование": "trade_name",
    "торговое наименование препарата": "trade_name",
    "торговое название": "trade_name",
    "торговое название препарата": "trade_name",
    "наименование препарата": "trade_name",
    "международное непатентованное наименование": "mnn",
    "международное непатентованное или группировочное наименование": "mnn",
    "международное непатентованное наименование (мнн)": "mnn",
    "мнн": "mnn",
    "международное название": "mnn",
    "действующее вещество": "mnn",
    "действующее вещество (мнн)": "mnn",
    "действующие вещества": "mnn",
    "активное вещество": "mnn",
    "активные вещества": "mnn",
    # Форма выпуска
    "лекарственная форма": "dosage_form",
    "лекарственные формы": "dosage_form",
    "форма выпуска": "dosage_form",
    "форма выпуска/дозировка": "dosage_form",
    "форма выпуска и дозировка": "dosage_form",
    "формы выпуска": "dosage_form",
    "форма выпуска и упаковка": "release_form_and_packaging",
    "упаковка": "packaging",
    # Состав
    "состав": "composition",
    "состав препарата": "composition",
    "состав и форма выпуска препарата": "composition",
    "состав и форма выпуска": "composition",
    "вспомогательные вещества": "excipients",
    "вспомогательные компоненты": "excipients",
    "состав оболочки": "coating_composition",
    # Производитель
    "производитель": "manufacturer",
    "изготовитель": "manufacturer",
    # АТХ
    "атх": "atx_code",
    "код атх": "atx_code",
    "код atx": "atx_code",
    "atx код": "atx_code",
    "код по атх": "atx_code",
    "фармакотерапевтическая группа": "pharmacological_group",
    "клинико-фармакологическая группа": "pharmacological_group",
    "фармакологическая группа": "pharmacological_group",
    "фармакологические группы": "pharmacological_group",
    # Фармакология
    "фармакологические свойства": "pharmacological_properties",
    "фармакологическое действие": "pharmacological_action",
    "фармакодинамика": "pharmacodynamics",
    "фармакодинамика:": "pharmacodynamics",
    "механизм действия": "mechanism_of_action",
    "фармакокинетика": "pharmacokinetics",
    "фармакокинетика препарата": "pharmacokinetics",
    "описание": "description",
    # Показания
    "показания": "indications",
    "показания к применению": "indications",
    "показания для применения": "indications",
    "показания к применению препарата": "indications",
    "инструкция по применению": "dosage_and_administration",
    "способ применения": "dosage_and_administration",
    "способ применения и дозы": "dosage_and_administration",
    "способ применения и дозировка": "dosage_and_administration",
    "дозировка": "dosage_and_administration",
    "режим дозирования": "dosage_and_administration",
    "применение": "dosage_and_administration",
    "применение и дозы": "dosage_and_administration",
    "рекомендации по применению": "dosage_and_administration",
    # Противопоказания
    "противопоказания": "contraindications",
    "противопоказания к применению": "contraindications",
    "абсолютные противопоказания": "contraindications_absolute",
    "с осторожностью": "use_with_caution",
    "с осторожностью применять": "use_with_caution",
    # Побочные эффекты
    "побочные действия": "side_effects",
    "побочные эффекты": "side_effects",
    "нежелательные реакции": "side_effects",
    "нежелательные явления": "side_effects",
    "нежелательные побочные реакции": "side_effects",
    # Взаимодействие
    "взаимодействие": "interactions",
    "взаимодействие с другими препаратами": "interactions",
    "взаимодействие с другими лекарственными средствами": "interactions",
    "взаимодействие с другими лекарствами": "interactions",
    "лекарственное взаимодействие": "interactions",
    "лекарственные взаимодействия": "interactions",
    "список взаимодействия лс": "interactions",
    # Передозировка
    "передозировка": "overdose",
    "симптомы передозировки": "overdose_symptoms",
    "симптомы": "overdose_symptoms",
    "лечение": "overdose_treatment",
    "лечение передозировки": "overdose_treatment",
    # Особые указания
    "особые указания": "special_instructions",
    "предупреждения": "special_instructions",
    "примечание": "notes",
    "примечания": "notes",
    "влияние на способность управлять транспортными средствами и механизмами": "driving_ability",
    "влияние на способность управлять трансп. ср. и мех.": "driving_ability",
    "влияние на вождение": "driving_ability",
    # Беременность
    "беременность и лактация": "pregnancy_and_lactation",
    "применение при беременности и в период кормления грудью": "pregnancy_and_lactation",
    "применение при беременности и грудном вскармливании": "pregnancy_and_lactation",
    "категория риска для беременных": "pregnancy_category",
    "беременность": "pregnancy_and_lactation",
    "кормление грудью": "pregnancy_and_lactation",
    # Специальные группы
    "применение в детском возрасте": "pediatric_use",
    "применение у детей": "pediatric_use",
    "детский возраст": "pediatric_use",
    "применение у пожилых пациентов": "geriatric_use",
    "применение при нарушениях функции почек": "renal_impairment",
    "применение при нарушениях функции печени": "hepatic_impairment",
    "нарушения функции почек": "renal_impairment",
    "нарушения функции печени": "hepatic_impairment",
    # Хранение / упаковка
    "условия хранения": "storage_conditions",
    "условия и срок хранения": "storage_conditions",
    "срок годности": "shelf_life",
    "срок хранения": "shelf_life",
    "условия отпуска": "dispensing_conditions",
    "условия отпуска из аптек": "dispensing_conditions",
    "отпуск по рецепту": "dispensing_conditions",
    # Синонимы / аналоги
    "синонимы": "synonyms",
    "аналоги": "analogs",
    "торговые аналоги": "analogs",
    # Дополнительные идентификаторы
    "латинское название": "mnn",
    "латинское наименование": "mnn",
    # АТХ — дополнительные варианты
    "атх код": "atx_code",
    "классификатор атх": "atx_code",
    # Состав — дополнительные варианты
    "вспомогательное вещество": "excipients",
    "вещества": "excipients",
    "состав пленочной оболочки": "coating_composition",
    "состав оболочки таблетки": "coating_composition",
    # Отпуск
    "отпуск из аптеки": "dispensing_conditions",
    "отпуск": "dispensing_conditions",
    # Подсекции побочных эффектов — все сводим в side_effects
    # Используется специальный префикс __append__ для конкатенации значений
    "со стороны пищеварительной системы": "__append__side_effects",
    "со стороны желудочно-кишечного тракта": "__append__side_effects",
    "нарушения со стороны желудочно-кишечного тракта": "__append__side_effects",
    "со стороны нервной системы": "__append__side_effects",
    "нарушения со стороны нервной системы": "__append__side_effects",
    "со стороны цнс": "__append__side_effects",
    "со стороны сердечно-сосудистой системы": "__append__side_effects",
    "нарушения со стороны сердца": "__append__side_effects",
    "нарушения со стороны сосудов": "__append__side_effects",
    "со стороны дыхательной системы": "__append__side_effects",
    "нарушения со стороны дыхательной системы, органов грудной клетки и средостения": "__append__side_effects",
    "со стороны иммунной системы": "__append__side_effects",
    "нарушения со стороны иммунной системы": "__append__side_effects",
    "со стороны мочевыделительной системы": "__append__side_effects",
    "нарушения со стороны почек и мочевыводящих путей": "__append__side_effects",
    "со стороны системы кроветворения": "__append__side_effects",
    "со стороны органов кроветворения": "__append__side_effects",
    "со стороны органа зрения": "__append__side_effects",
    "нарушения со стороны органа зрения": "__append__side_effects",
    "со стороны кожи и подкожных тканей": "__append__side_effects",
    "нарушения со стороны кожи и подкожных тканей": "__append__side_effects",
    "со стороны кожных покровов": "__append__side_effects",
    "со стороны обмена веществ": "__append__side_effects",
    "нарушения со стороны обмена веществ и питания": "__append__side_effects",
    "со стороны костно-мышечной системы": "__append__side_effects",
    "нарушения со стороны скелетно-мышечной и соединительной ткани": "__append__side_effects",
    "со стороны печени и желчевыводящих путей": "__append__side_effects",
    "нарушения со стороны печени и желчевыводящих путей": "__append__side_effects",
    "со стороны органов чувств": "__append__side_effects",
    "нарушения психики": "__append__side_effects",
    "со стороны эндокринной системы": "__append__side_effects",
    "аллергические реакции": "__append__side_effects",
    "местные реакции": "__append__side_effects",
    "общие реакции": "__append__side_effects",
    "общие расстройства и нарушения в месте введения": "__append__side_effects",
    "лабораторные показатели": "__append__side_effects",
    "лабораторные и инструментальные данные": "__append__side_effects",
    "прочие": "__append__side_effects",
    "прочие реакции": "__append__side_effects",
    # Частота побочных эффектов — тоже в side_effects
    "очень часто": "__append__side_effects",
    "часто": "__append__side_effects",
    "нечасто": "__append__side_effects",
    "редко": "__append__side_effects",
    "очень редко": "__append__side_effects",
    "возможно": "__append__side_effects",
    # Показания расширенные
    "гипербарическая оксигенация": "__append__indications",
    "различные заболевания, сопровождающиеся гипоксией": "__append__indications",
}

# Поля, которые относятся к «содержательным» (для оценки качества парсинга)
MEANINGFUL_FIELDS = {
    "trade_name", "mnn", "indications", "contraindications",
    "pharmacological_action", "pharmacodynamics", "pharmacokinetics",
    "dosage_and_administration", "side_effects", "interactions",
    "overdose", "special_instructions", "composition",
}

# Шумовые строки-маркеры навигации/рекламы — если их много в начале текста,
# значит перед нами «хвост» сайта, а не инструкция.
NOISE_PATTERNS = re.compile(
    r"^("
    r"лекарства\s*$|заболевания\s*$|консультации?\s*$|поиск в аптеках\s*$"
    r"|справочник\s*$|болезни\s*$|новости\s*$|статьи\s*$|контакты?\s*$"
    r"|читайте также\s*$|воспользуйтесь поиском\s*$|оцените статью\s*$"
    r"|поделиться\s*$|рейтинг\s*$|цены\s*$|купить\s*$|отзывы\s*$"
    r"|добавить отзыв\s*$|ваше имя\s*$|ваш отзыв\s*$|ваша оценка\s*$"
    r"|на главную\s*$|инструкция\s*$|аналоги\s*$|цены и наличие\s*$"
    r"|© \d{4}.*$|обратная связь\s*$|понятно\s*$|наверх\s*$"
    r"|москва\s*$|россия\s*$|украина\s*$|беларусь\s*$|казахстан\s*$"
    r"|германия\s*$|медвизор\s*$|мы в соцсетях\s*$"
    r"|образование\s*$|знаете ли вы, что\s*$|знаете ли вы\s*$"
    r"|поиск в аптеках\s*$|вопрос-ответ\s*$|видео\s*$|источники\s*$"
    r"|показать еще\s*$|показать ещё\s*$|скрыть\s*$|все разделы\s*$"
    r"|бесплатные консультации\s*$|нормативные документы\s*$"
    r"|о службе\s*$|найти\s*$|все проекты\s*$|о компании\s*$"
    r"|редакция\s*$|поддержка\s*$|реклама\s*$|условия использования\s*$"
    r"|политика конфиденциальности\s*$|о технологиях рекомендаций\s*$"
    r"|кликните для выбора на звездочку\s*$|ваша оценка\s*$"
    r")",
    re.IGNORECASE,
)

# Строки, с которых начинается хвост (отзывы, футер) — всё после них отбрасывается
TAIL_SENTINELS = re.compile(
    r"^(отзывы\s*(\(\d+\))?$|источник:\s*|© \d{4}|обратная связь$"
    r"|добавить отзыв$|ваше имя$|кликните для выбора"
    r"|оставить отзыв$|написать отзыв$|все отзывы$"
    r"|читайте нас в социальных сетях$|мы в социальных сетях$"
    r")",
    re.IGNORECASE,
)


@dataclass
class ParsedInstruction:
    """Все извлечённые поля инструкции.

    Поля именованы в snake_case; None означает «не найдено».
    """
    source_id: int
    reg_number: Optional[str] = None
    trade_name: Optional[str] = None
    mnn: Optional[str] = None
    synonyms: Optional[str] = None
    dosage_form: Optional[str] = None
    release_form_and_packaging: Optional[str] = None
    packaging: Optional[str] = None
    manufacturer: Optional[str] = None
    atx_code: Optional[str] = None
    pharmacological_group: Optional[str] = None
    pharmacological_properties: Optional[str] = None
    pharmacological_action: Optional[str] = None
    pharmacodynamics: Optional[str] = None
    mechanism_of_action: Optional[str] = None
    pharmacokinetics: Optional[str] = None
    composition: Optional[str] = None
    excipients: Optional[str] = None
    description: Optional[str] = None
    indications: Optional[str] = None
    contraindications: Optional[str] = None
    contraindications_absolute: Optional[str] = None
    use_with_caution: Optional[str] = None
    dosage_and_administration: Optional[str] = None
    side_effects: Optional[str] = None
    interactions: Optional[str] = None
    overdose: Optional[str] = None
    overdose_symptoms: Optional[str] = None
    overdose_treatment: Optional[str] = None
    special_instructions: Optional[str] = None
    notes: Optional[str] = None
    driving_ability: Optional[str] = None
    pregnancy_and_lactation: Optional[str] = None
    pregnancy_category: Optional[str] = None
    pediatric_use: Optional[str] = None
    geriatric_use: Optional[str] = None
    renal_impairment: Optional[str] = None
    hepatic_impairment: Optional[str] = None
    storage_conditions: Optional[str] = None
    shelf_life: Optional[str] = None
    dispensing_conditions: Optional[str] = None
    coating_composition: Optional[str] = None
    analogs: Optional[str] = None
    # Метаданные парсинга
    parse_method: str = "regex"
    filled_fields_count: int = 0
    extra_fields_json: Optional[str] = None  # поля, не вошедшие в схему


def _normalize_key(raw: str) -> str:
    """Приводим заголовок к нижнему регистру, убираем лишние символы."""
    return raw.strip().rstrip(":").lower().strip()


# Значения, которые не являются реальным содержимым
_JUNK_VALUES = re.compile(
    r"^("
    r"~|n/a|нет данных?|нет|данные отсутствуют|не установлено"
    r"|не определено|отсутствует|—|-|–|\.|не указано"
    r")$",
    re.IGNORECASE,
)

# Строки UI-шума внутри значений
_UI_NOISE = re.compile(
    r"(показать ещё|показать еще|показать все|скрыть|подробнее"
    r"|данные из справочника есклп|оформить подписку"
    r"|в бесплатной версии|коммерческой версии|нажмите для)",
    re.IGNORECASE,
)

# Строки оглавления вида «1.», «2.», «1. Заголовок», «10.» — пустые нумерованные пункты
_TOC_LINE = re.compile(r"^\d{1,2}\.\s*$")

# Нумерованный пункт оглавления с текстом: «1. Форма выпуска и состав»
_TOC_ITEM = re.compile(r"^\d{1,2}\.\s+(.+)$")

# Мусор в reg_number: лидирующее ':', даты вида «-DDMMYY», «от DD.MM.YYYY», пробелы
_REG_CLEANUP = re.compile(
    r"^\s*:?\s*"         # убираем лидирующий «:» и пробелы
    r"|"
    r"\s*[-–]\s*\d{6,}"  # убираем суффикс даты «-190210»
    r"|"
    r"\s+от\s+\d{2}\.\d{2}\.\d{4}.*$"  # убираем «от 26.05.2005»
    r"|"
    r"\s+\d{4}$",        # убираем год в конце
    re.IGNORECASE,
)

# Мусор в mnn: дозировки «- 250 мг», «(в пересчете ...)», «– 500 мг»
_MNN_DOSAGE = re.compile(
    r"\s*[-–(]\s*(?:в пересчете[^)]*\))?"
    r"\s*[\d,.\s]*(?:мг|г|мкг|ме|ед)\b.*",
    re.IGNORECASE,
)

# Мусор в trade_name: «Международное непатентованное название Нет», «МНН: ...»
_TRADE_NAME_SUFFIX = re.compile(
    r"\s+(?:Международное\s+(?:непатентованное\s+)?(?:название|наименование)"
    r"|МНН|INN|ATC|АТХ).*$",
    re.IGNORECASE,
)


def _normalize_reg_number(raw: str) -> str | None:
    """Очищаем регистрационный номер от артефактов парсинга.

    Примеры входных данных → ожидаемый результат:
    ': ЛС-000692'           → 'ЛС-000692'
    'Р N003048/01'          → 'Р N003048/01'   (сам по себе корректен)
    'ЛС-001247/10-190210'   → 'ЛСР-001247/10'
    'ЛС-000325 от 26.05.2005' → 'ЛС-000325'
    '74 Однородная масса...' → None            (мусор, не рег. номер)
    """
    v = raw.strip().lstrip(":").strip()
    # Убираем суффиксы дат
    v = re.sub(r"\s*-\s*\d{6,}$", "", v)
    v = re.sub(r"\s+от\s+\d{2}\.\d{2}\.\d{4}.*$", "", v, flags=re.IGNORECASE)
    v = re.sub(r"\s+\d{4}$", "", v)
    v = v.strip()
    # Если после очистки осталось что-то длиннее 30 символов — скорее всего мусор
    if not v or len(v) > 30:
        return None
    # Должен содержать букву и цифру
    if not re.search(r"[а-яёА-ЯЁa-zA-Z]", v) or not re.search(r"\d", v):
        return None
    return v


def _normalize_mnn(raw: str) -> str | None:
    """Очищаем МНН от дозировок, лишних пояснений и мусора.

    Примеры:
    'Натрия нуклеинат (в пересчете на сухое вещество) - 250 мг.'
        → 'Натрия нуклеинат'
    'кофеин (в пересчете на 100%) – 80 мг, натрия бензоат – 120 мг'
        → None  (это состав, не МНН)
    'экстракт из культуры термофильного штамма...'
        → 'экстракт из культуры термофильного штамма...' (оставляем — биол. препарат)
    """
    v = raw.strip().lstrip(":").strip()
    # Убираем дозировки: «- 250 мг», «– 500 мг», «(в пересчете на ...) 80 мг»
    v = re.sub(
        r"\s*[-–(]\s*(?:в пересчете[^)]*\))?\s*[\d,.\s]*(?:мг|г|мкг|мл|ме|ед)\b.*",
        "", v, flags=re.IGNORECASE,
    )
    # Убираем лидирующий «-»
    v = v.strip().lstrip("-–").strip()
    if not v or _JUNK_VALUES.match(v):
        return None
    # Если больше 200 символов — это скорее всего состав препарата, не МНН
    if len(v) > 200:
        return None
    # Если содержит числа с единицами после очистки — состав
    if re.search(r"\d\s*(?:мг|г|мл)\b", v, re.IGNORECASE):
        return None
    return v


def _normalize_trade_name(raw: str) -> str | None:
    """Убираем мусор после торгового названия.

    Некоторые источники (казахстанские регуляторные) включают в поле trade_name
    строку «Международное непатентованное название Нет» или «МНН: Ибупрофен».
    """
    if not raw:
        return None
    v = _TRADE_NAME_SUFFIX.sub("", raw).strip().rstrip(".,;")
    return v if v else None


def _normalize_sentence_case(text: str) -> str:
    """Нормализует регистр первого символа предложения.

    Если текст начинается со строчной — делаем первую букву заглавной.
    Не трогаем аббревиатуры (всё заглавными).
    """
    if not text:
        return text
    # Если вся строка — аббревиатура или цифры, не трогаем
    if text.isupper() or text[0].isdigit():
        return text
    return text[0].upper() + text[1:]


def _clean_value(value: str) -> str | None:
    """Очищаем значение от артефактов парсинга.

    - Убираем ведущие дефисы/тире и двоеточия.
    - Отфильтровываем заглушки («~», «нет данных» и т.п.).
    - Убираем строки UI-шума.
    - Нормализуем регистр первого символа.
    """
    v = value.strip().lstrip("- –:").strip()
    if not v or _JUNK_VALUES.match(v):
        return None
    # Убираем строки UI-шума
    lines = [ln for ln in v.splitlines() if not _UI_NOISE.search(ln)]
    cleaned = " ".join(ln.strip() for ln in lines if ln.strip())
    if not cleaned:
        return None
    return _normalize_sentence_case(cleaned)


def _find_content_start(lines: list[str]) -> int:
    """Находит индекс строки, с которой начинается реальное содержимое инструкции.

    Эвристика: первая строка-заголовок из SYNONYM_MAP, за которой следует
    непустая строка-значение длиной ≥ 20 символов. Это отличает реальные
    секции инструкции от блоков оглавления (где заголовки идут подряд без значений).
    """
    for i in range(len(lines) - 1):
        stripped = lines[i].strip()
        norm = _normalize_key(stripped)
        if norm not in SYNONYM_MAP:
            continue
        # Смотрим следующие 3 строки — есть ли хоть одна с содержимым
        following = [lines[j].strip() for j in range(i + 1, min(i + 4, len(lines)))]
        has_content = any(
            len(ln) >= 20
            and _normalize_key(ln) not in SYNONYM_MAP
            and not _TOC_LINE.match(ln)
            for ln in following
        )
        if has_content:
            return i
    return 0


def _strip_noise_header(text: str) -> str:
    """Убираем шапку/хвост сайта и строки оглавления «1. 2. 3.».

    Алгоритм:
    - Находим первую реальную секцию через _find_content_start.
    - NOISE_PATTERNS фильтруется на всём протяжении текста.
    - Строки вида «1.» / «2.» (нумерация оглавления без содержимого) удаляются.
    - TAIL_SENTINELS обрезают хвост только ПОСЛЕ начала содержимого.
    """
    lines = text.splitlines()
    content_start = _find_content_start(lines)
    cleaned: list[str] = []
    in_content = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i >= content_start:
            in_content = True
        if in_content and TAIL_SENTINELS.match(stripped):
            break
        # Пустые строки оглавления «1.», «2.» отбрасываем везде
        if _TOC_LINE.match(stripped):
            continue
        if not NOISE_PATTERNS.match(stripped):
            cleaned.append(line)
    return "\n".join(cleaned)


def _extract_name_from_header(text: str, db_trade_name: str) -> str | None:
    """Извлекаем торговое название из первых строк инструкции.

    Эвристика: первая непустая, не-шумовая строка длиной 2–80 символов
    без двоеточия в конце — это название препарата.
    """
    lines = text.splitlines()
    for line in lines[:15]:
        s = line.strip()
        # Рейтинг/оценка типа "(4.0 / 1)" — пропускаем
        if re.match(r"^\([\d.,\s/]+\)$", s):
            continue
        if 2 <= len(s) <= 80 and not s.endswith(":") and not NOISE_PATTERNS.match(s):
            # Строка не должна состоять только из цифр/символов
            if re.search(r"[а-яёА-ЯЁa-zA-Z]", s):
                return s
    return db_trade_name or None


def _parse_with_regex(
    raw_text: str,
    db_id: int,
    db_trade_name: str,
    db_mnn: str,
    db_reg_number: str,
) -> ParsedInstruction:
    """Основной regex-парсер.

    Алгоритм:
    1. Очищаем текст от мусора (навигация, футер).
    2. Разбиваем на строки и ищем строки-«заголовки»:
       - строка заканчивается на «:» и её нормализованная версия есть в SYNONYM_MAP, или
       - строка целиком является заголовком (следующая строка — значение).
    3. Собираем «span» от заголовка до следующего заголовка как значение секции.
    4. Нормализуем ключ через SYNONYM_MAP и записываем в ParsedInstruction.
    """
    cleaned = _strip_noise_header(raw_text)
    lines = cleaned.splitlines()

    # Находим позиции заголовков
    header_positions: list[tuple[int, str]] = []  # (line_index, canonical_field)
    extra_sections: dict[str, list[str]] = {}

    for idx, line in enumerate(lines):
        raw_header = line.strip()
        if not raw_header:
            continue
        norm = _normalize_key(raw_header)
        if norm in SYNONYM_MAP:
            header_positions.append((idx, SYNONYM_MAP[norm]))
        elif raw_header.endswith(":") and 3 < len(norm) < 80:
            # Неизвестный заголовок с двоеточием — сохраняем в extra
            header_positions.append((idx, f"__extra__{norm}"))


    # Собираем значения для каждого заголовка
    sections: dict[str, list[str]] = {}
    for i, (pos, field_name) in enumerate(header_positions):
        next_pos = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(lines)
        value_lines = [ln.strip() for ln in lines[pos + 1:next_pos] if ln.strip()]
        value = " ".join(value_lines).strip()
        if not value:
            continue
        if field_name.startswith("__append__"):
            # Накапливаем подсекции (напр. подразделы побочных эффектов) в целевое поле
            target = field_name[len("__append__"):]
            if target not in sections:
                sections[target] = value_lines
            else:
                sections[target].extend(value_lines)
        elif field_name.startswith("__extra__"):
            clean_name = field_name[len("__extra__"):]
            if clean_name not in extra_sections:
                extra_sections[clean_name] = value_lines
        elif field_name not in sections:
            # Берём первое вхождение поля — оно обычно самое полное для дублирующих сайтов
            sections[field_name] = value_lines

    # Специальная обработка блока «Передозировка» — он часто содержит
    # подраздел «Симптомы:» и «Лечение:» внутри себя
    if "overdose" in sections:
        overdose_text = " ".join(sections["overdose"])
        # Ищем паттерн «Симптомы:» внутри текста передозировки
        sym_match = re.search(r"симптомы[:\s]+(.+?)(?:лечение[:\s]|$)", overdose_text, re.IGNORECASE | re.DOTALL)
        treat_match = re.search(r"лечение[:\s]+(.+)$", overdose_text, re.IGNORECASE | re.DOTALL)
        if sym_match and "overdose_symptoms" not in sections:
            sections["overdose_symptoms"] = [sym_match.group(1).strip()]
        if treat_match and "overdose_treatment" not in sections:
            sections["overdose_treatment"] = [treat_match.group(1).strip()]

    def _get(key: str) -> str | None:
        val = sections.get(key)
        if val is None:
            return None
        joined = " ".join(val).strip()
        return _clean_value(joined)

    # Торговое название: из секций → из БД → из шапки текста; затем нормализуем
    raw_trade = _get("trade_name") or db_trade_name or _extract_name_from_header(cleaned, db_trade_name)
    resolved_trade_name = _normalize_trade_name(raw_trade) if raw_trade else None

    # МНН: из секций → из БД; убираем дозировки и мусор
    raw_mnn = _get("mnn") or _clean_value(db_mnn)
    resolved_mnn = _normalize_mnn(raw_mnn) if raw_mnn else None

    # Регистрационный номер: из секций → из БД; нормализуем формат
    raw_reg = _get("reg_number") or db_reg_number
    resolved_reg = _normalize_reg_number(raw_reg) if raw_reg else None

    # Производитель: если значение слишком длинное или содержит фразы из шапки документа
    raw_manufacturer = _get("manufacturer")
    resolved_manufacturer = (
        raw_manufacturer
        if raw_manufacturer
        and len(raw_manufacturer) <= 200
        and not re.search(
            r"по медицинскому применению|минздрав|утверждена",
            raw_manufacturer, re.IGNORECASE,
        )
        else None
    )

    # Состав: объединяем основной состав + вспомогательные вещества в одно поле,
    # если основной состав — это только заголовочная фраза без содержимого
    raw_composition = _get("composition")
    raw_excipients = _get("excipients")
    if raw_composition and len(raw_composition) < 25 and raw_excipients:
        # «100 г препарата содержит» без тела — дополняем вспомогательными
        resolved_composition = f"{raw_composition}; {raw_excipients}"
    else:
        resolved_composition = raw_composition

    parsed = ParsedInstruction(
        source_id=db_id,
        reg_number=resolved_reg,
        trade_name=resolved_trade_name,
        mnn=resolved_mnn,
        synonyms=_get("synonyms"),
        dosage_form=_get("dosage_form"),
        release_form_and_packaging=_get("release_form_and_packaging"),
        packaging=_get("packaging"),
        manufacturer=resolved_manufacturer,
        atx_code=_get("atx_code"),
        pharmacological_group=_get("pharmacological_group"),
        pharmacological_properties=_get("pharmacological_properties"),
        pharmacological_action=_get("pharmacological_action"),
        pharmacodynamics=_get("pharmacodynamics"),
        mechanism_of_action=_get("mechanism_of_action"),
        pharmacokinetics=_get("pharmacokinetics"),
        composition=resolved_composition,
        excipients=raw_excipients,
        description=_get("description"),
        indications=_get("indications"),
        contraindications=_get("contraindications"),
        contraindications_absolute=_get("contraindications_absolute"),
        use_with_caution=_get("use_with_caution"),
        dosage_and_administration=_get("dosage_and_administration"),
        side_effects=_get("side_effects"),
        interactions=_get("interactions"),
        overdose=_get("overdose"),
        overdose_symptoms=_get("overdose_symptoms"),
        overdose_treatment=_get("overdose_treatment"),
        special_instructions=_get("special_instructions"),
        notes=_get("notes"),
        driving_ability=_get("driving_ability"),
        pregnancy_and_lactation=_get("pregnancy_and_lactation"),
        pregnancy_category=_get("pregnancy_category"),
        pediatric_use=_get("pediatric_use"),
        geriatric_use=_get("geriatric_use"),
        renal_impairment=_get("renal_impairment"),
        hepatic_impairment=_get("hepatic_impairment"),
        storage_conditions=_get("storage_conditions"),
        shelf_life=_get("shelf_life"),
        dispensing_conditions=_get("dispensing_conditions"),
        coating_composition=_get("coating_composition"),
        analogs=_get("analogs"),
        extra_fields_json=json.dumps(
            {k: " ".join(v) for k, v in extra_sections.items()},
            ensure_ascii=False,
        ) if extra_sections else None,
    )

    # Считаем заполненные содержательные поля
    parsed.filled_fields_count = sum(
        1 for f in MEANINGFUL_FIELDS
        if getattr(parsed, f, None)
    )
    return parsed


def _parse_with_llm(
    raw_text: str,
    db_id: int,
    db_trade_name: str,
    db_mnn: str,
    db_reg_number: str,
    model: str = "huihui_ai/qwen3-abliterated:14b",
) -> ParsedInstruction:
    """LLM fallback через ollama.

    Отправляет очищенный текст (до 8000 символов) в модель и просит вернуть JSON
    со структурированными полями. Возвращает ParsedInstruction с parse_method='llm'.
    """
    try:
        import ollama  # noqa: PLC0415 — импорт здесь намеренно (опциональная зависимость)
    except ImportError:
        logging.warning("ollama python package не установлен; LLM fallback недоступен")
        result = _parse_with_regex(raw_text, db_id, db_trade_name, db_mnn, db_reg_number)
        result.parse_method = "regex_only"
        return result

    cleaned = _strip_noise_header(raw_text)
    truncated = cleaned[:8000]

    prompt = f"""Ты — медицинский аналитик. Извлеки из текста инструкции по медицинскому применению препарата структурированные поля в формате JSON.
Верни ТОЛЬКО JSON объект без пояснений. Используй ключи на английском (snake_case).

Нужные ключи (если информации нет — не включай ключ):
trade_name, reg_number, mnn, synonyms, dosage_form, packaging, manufacturer, atx_code,
pharmacological_group, pharmacological_action, pharmacodynamics, pharmacokinetics,
composition, indications, contraindications, use_with_caution, dosage_and_administration,
side_effects, interactions, overdose, overdose_symptoms, overdose_treatment,
special_instructions, notes, pregnancy_and_lactation, pediatric_use, storage_conditions,
shelf_life, dispensing_conditions

ТЕКСТ ИНСТРУКЦИИ:
{truncated}

JSON:"""

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 2048},
        )
        raw_json = response["message"]["content"].strip()
        # Вырезаем JSON из потенциальных markdown-блоков
        json_match = re.search(r"\{.*\}", raw_json, re.DOTALL)
        if not json_match:
            raise ValueError("JSON не найден в ответе модели")
        data: dict[str, str] = json.loads(json_match.group())
    except Exception as exc:
        logging.warning("LLM fallback failed for id=%d: %s", db_id, exc)
        result = _parse_with_regex(raw_text, db_id, db_trade_name, db_mnn, db_reg_number)
        result.parse_method = "regex_fallback_after_llm_error"
        return result

    # Строим ParsedInstruction из dict, игнорируя неизвестные ключи
    valid_fields = {f for f in ParsedInstruction.__dataclass_fields__ if f not in ("source_id", "parse_method", "filled_fields_count", "extra_fields_json")}
    known = {k: v for k, v in data.items() if k in valid_fields and isinstance(v, str)}
    extra = {k: v for k, v in data.items() if k not in valid_fields and isinstance(v, str)}

    parsed = ParsedInstruction(source_id=db_id, **known)  # type: ignore[arg-type]
    parsed.reg_number = parsed.reg_number or db_reg_number or None
    parsed.trade_name = parsed.trade_name or db_trade_name or None
    parsed.mnn = parsed.mnn or db_mnn or None
    parsed.parse_method = "llm"
    parsed.filled_fields_count = sum(1 for f in MEANINGFUL_FIELDS if getattr(parsed, f, None))
    parsed.extra_fields_json = json.dumps(extra, ensure_ascii=False) if extra else None
    return parsed


# ──────────────────────────────────────────────────────────────
# DDL для целевой таблицы
# ──────────────────────────────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS drug_parsed_instructions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id                   INTEGER NOT NULL UNIQUE
                                    REFERENCES drug_instructions(id) ON DELETE CASCADE,
    reg_number                  TEXT,
    trade_name                  TEXT,
    mnn                         TEXT,
    synonyms                    TEXT,
    dosage_form                 TEXT,
    release_form_and_packaging  TEXT,
    packaging                   TEXT,
    manufacturer                TEXT,
    atx_code                    TEXT,
    pharmacological_group       TEXT,
    pharmacological_properties  TEXT,
    pharmacological_action      TEXT,
    pharmacodynamics            TEXT,
    mechanism_of_action         TEXT,
    pharmacokinetics            TEXT,
    composition                 TEXT,
    excipients                  TEXT,
    description                 TEXT,
    indications                 TEXT,
    contraindications           TEXT,
    contraindications_absolute  TEXT,
    use_with_caution            TEXT,
    dosage_and_administration   TEXT,
    side_effects                TEXT,
    interactions                TEXT,
    overdose                    TEXT,
    overdose_symptoms           TEXT,
    overdose_treatment          TEXT,
    special_instructions        TEXT,
    notes                       TEXT,
    driving_ability             TEXT,
    pregnancy_and_lactation     TEXT,
    pregnancy_category          TEXT,
    pediatric_use               TEXT,
    geriatric_use               TEXT,
    renal_impairment            TEXT,
    hepatic_impairment          TEXT,
    storage_conditions          TEXT,
    shelf_life                  TEXT,
    dispensing_conditions       TEXT,
    coating_composition         TEXT,
    analogs                     TEXT,
    parse_method                TEXT NOT NULL DEFAULT 'regex',
    filled_fields_count         INTEGER NOT NULL DEFAULT 0,
    extra_fields_json           TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_drug_parsed_source ON drug_parsed_instructions(source_id);
CREATE INDEX IF NOT EXISTS idx_drug_parsed_trade   ON drug_parsed_instructions(trade_name);
CREATE INDEX IF NOT EXISTS idx_drug_parsed_mnn     ON drug_parsed_instructions(mnn);
"""

UPSERT_SQL = """
INSERT INTO drug_parsed_instructions (
    source_id, reg_number, trade_name, mnn, synonyms, dosage_form,
    release_form_and_packaging, packaging, manufacturer, atx_code,
    pharmacological_group, pharmacological_properties, pharmacological_action,
    pharmacodynamics, mechanism_of_action, pharmacokinetics, composition, excipients,
    description, indications, contraindications, contraindications_absolute,
    use_with_caution, dosage_and_administration, side_effects, interactions,
    overdose, overdose_symptoms, overdose_treatment, special_instructions, notes,
    driving_ability, pregnancy_and_lactation, pregnancy_category, pediatric_use,
    geriatric_use, renal_impairment, hepatic_impairment, storage_conditions, shelf_life,
    dispensing_conditions, coating_composition, analogs,
    parse_method, filled_fields_count, extra_fields_json, updated_at
) VALUES (
    :source_id, :reg_number, :trade_name, :mnn, :synonyms, :dosage_form,
    :release_form_and_packaging, :packaging, :manufacturer, :atx_code,
    :pharmacological_group, :pharmacological_properties, :pharmacological_action,
    :pharmacodynamics, :mechanism_of_action, :pharmacokinetics, :composition, :excipients,
    :description, :indications, :contraindications, :contraindications_absolute,
    :use_with_caution, :dosage_and_administration, :side_effects, :interactions,
    :overdose, :overdose_symptoms, :overdose_treatment, :special_instructions, :notes,
    :driving_ability, :pregnancy_and_lactation, :pregnancy_category, :pediatric_use,
    :geriatric_use, :renal_impairment, :hepatic_impairment, :storage_conditions, :shelf_life,
    :dispensing_conditions, :coating_composition, :analogs,
    :parse_method, :filled_fields_count, :extra_fields_json, CURRENT_TIMESTAMP
)
ON CONFLICT(source_id) DO UPDATE SET
    reg_number=excluded.reg_number, trade_name=excluded.trade_name, mnn=excluded.mnn,
    synonyms=excluded.synonyms, dosage_form=excluded.dosage_form,
    release_form_and_packaging=excluded.release_form_and_packaging,
    packaging=excluded.packaging, manufacturer=excluded.manufacturer,
    atx_code=excluded.atx_code, pharmacological_group=excluded.pharmacological_group,
    pharmacological_properties=excluded.pharmacological_properties,
    pharmacological_action=excluded.pharmacological_action,
    pharmacodynamics=excluded.pharmacodynamics, mechanism_of_action=excluded.mechanism_of_action,
    pharmacokinetics=excluded.pharmacokinetics, composition=excluded.composition,
    excipients=excluded.excipients, description=excluded.description,
    indications=excluded.indications, contraindications=excluded.contraindications,
    contraindications_absolute=excluded.contraindications_absolute,
    use_with_caution=excluded.use_with_caution,
    dosage_and_administration=excluded.dosage_and_administration,
    side_effects=excluded.side_effects, interactions=excluded.interactions,
    overdose=excluded.overdose, overdose_symptoms=excluded.overdose_symptoms,
    overdose_treatment=excluded.overdose_treatment,
    special_instructions=excluded.special_instructions, notes=excluded.notes,
    driving_ability=excluded.driving_ability,
    pregnancy_and_lactation=excluded.pregnancy_and_lactation,
    pregnancy_category=excluded.pregnancy_category, pediatric_use=excluded.pediatric_use,
    geriatric_use=excluded.geriatric_use, renal_impairment=excluded.renal_impairment,
    hepatic_impairment=excluded.hepatic_impairment, storage_conditions=excluded.storage_conditions,
    shelf_life=excluded.shelf_life, dispensing_conditions=excluded.dispensing_conditions,
    coating_composition=excluded.coating_composition, analogs=excluded.analogs,
    parse_method=excluded.parse_method, filled_fields_count=excluded.filled_fields_count,
    extra_fields_json=excluded.extra_fields_json, updated_at=CURRENT_TIMESTAMP
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    for statement in CREATE_TABLE_SQL.strip().split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


app = typer.Typer(add_completion=False, help="Экстрактор структурированных полей инструкций лекарств")
console = Console()


@app.command()
def run(
    db_path: Path = typer.Option(DB_PATH, "--db", help="Путь к SQLite-базе данных"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Только парсинг без записи в БД"),
    limit: int = typer.Option(0, "--limit", help="Ограничить число обрабатываемых записей (0 = все)"),
    llm_fallback: bool = typer.Option(False, "--llm-fallback", help="Использовать LLM для записей с < 4 полями"),
    llm_model: str = typer.Option("huihui_ai/qwen3-abliterated:14b", "--llm-model", help="Модель ollama для fallback"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробный вывод"),
) -> None:
    """Разобрать raw_text из drug_instructions и записать поля в drug_parsed_instructions."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        stream=sys.stderr,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if not dry_run:
        _ensure_table(conn)

    query = """
        SELECT id, reg_number, trade_name, mnn, raw_text
        FROM drug_instructions
        WHERE raw_text IS NOT NULL AND LENGTH(raw_text) > 50
        ORDER BY id
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    console.print(f"[bold]Записей для обработки:[/bold] {len(rows)}")

    stats = {"total": 0, "good": 0, "poor": 0, "llm_used": 0, "errors": 0}
    start_ts = time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Обработка...", total=len(rows))

        for row in rows:
            stats["total"] += 1
            try:
                parsed = _parse_with_regex(
                    raw_text=row["raw_text"],
                    db_id=row["id"],
                    db_trade_name=row["trade_name"] or "",
                    db_mnn=row["mnn"] or "",
                    db_reg_number=row["reg_number"] or "",
                )

                if llm_fallback and parsed.filled_fields_count < MIN_FIELDS_THRESHOLD:
                    parsed = _parse_with_llm(
                        raw_text=row["raw_text"],
                        db_id=row["id"],
                        db_trade_name=row["trade_name"] or "",
                        db_mnn=row["mnn"] or "",
                        db_reg_number=row["reg_number"] or "",
                        model=llm_model,
                    )
                    stats["llm_used"] += 1

                if parsed.filled_fields_count >= MIN_FIELDS_THRESHOLD:
                    stats["good"] += 1
                else:
                    stats["poor"] += 1

                if not dry_run:
                    conn.execute(UPSERT_SQL, asdict(parsed))

            except Exception as exc:
                stats["errors"] += 1
                logging.error("Ошибка обработки id=%d: %s", row["id"], exc)

            progress.advance(task)

    if not dry_run:
        conn.commit()
    conn.close()

    elapsed = time.monotonic() - start_ts
    _print_stats(stats, elapsed)


def _print_stats(stats: dict[str, int], elapsed: float) -> None:
    table = Table(title="Результаты парсинга", show_header=True, header_style="bold cyan")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")

    good_pct = stats["good"] / stats["total"] * 100 if stats["total"] else 0
    table.add_row("Всего обработано", str(stats["total"]))
    table.add_row("Хороших записей (≥4 полей)", f"{stats['good']} ({good_pct:.1f}%)")
    table.add_row("Бедных записей (<4 полей)", str(stats["poor"]))
    table.add_row("LLM fallback использован", str(stats["llm_used"]))
    table.add_row("Ошибок", str(stats["errors"]))
    table.add_row("Время, сек", f"{elapsed:.1f}")

    console.print(table)


@app.command()
def inspect(
    db_path: Path = typer.Option(DB_PATH, "--db", help="Путь к SQLite-базе данных"),
    drug_id: int = typer.Argument(..., help="ID записи в drug_instructions"),
) -> None:
    """Показать извлечённые поля для одной записи (без записи в БД)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, reg_number, trade_name, mnn, raw_text FROM drug_instructions WHERE id=?",
        (drug_id,),
    ).fetchone()
    conn.close()

    if row is None:
        console.print(f"[red]Запись с id={drug_id} не найдена[/red]")
        raise typer.Exit(1)

    parsed = _parse_with_regex(
        raw_text=row["raw_text"] or "",
        db_id=row["id"],
        db_trade_name=row["trade_name"] or "",
        db_mnn=row["mnn"] or "",
        db_reg_number=row["reg_number"] or "",
    )

    table = Table(title=f"Запись id={drug_id}", show_header=True, header_style="bold green")
    table.add_column("Поле")
    table.add_column("Значение")

    for fname, fval in asdict(parsed).items():
        if fval is not None and fname not in ("source_id", "extra_fields_json"):
            preview = str(fval)[:120] + ("…" if len(str(fval)) > 120 else "")
            table.add_row(fname, preview)

    console.print(table)
    console.print(f"[bold]Заполненных содержательных полей:[/bold] {parsed.filled_fields_count}")
    if parsed.extra_fields_json:
        console.print(f"[dim]Дополнительные поля: {parsed.extra_fields_json[:200]}[/dim]")


if __name__ == "__main__":
    app()
