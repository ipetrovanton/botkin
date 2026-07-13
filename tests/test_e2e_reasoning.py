"""E2E-проверки medical reasoning через локальные uncensored LLM (Ollama).

Тестирует способность моделей:
  1. Анализировать лабораторные показатели — норма/отклонение, критичность.
  2. Формировать дифференциальный диагноз по симптомам и анализам.
  3. Предлагать рекомендации по лечению и дообследованию.
  4. Проверять назначения врача на соответствие стандартам.
  5. Интегрировать данные Garmin (пульс, сон, стресс) в стратегию лечения.

Модель задаётся через env REASONING_MODEL (по умолчанию huihui_ai/Qwen3.6-abliterated:27b).
Без запущенной Ollama — skip.

Запуск:
    REASONING_MODEL=huihui_ai/Qwen3.6-abliterated:27b uv run pytest -m reasoning -s
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import pytest

from botkin.llm.client import _detect_ollama_url, _is_url_reachable

try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

pytestmark = pytest.mark.reasoning

_REASONING_MODEL = os.getenv("REASONING_MODEL", "huihui_ai/Qwen3.6-abliterated:27b")
_REASONING_NUM_PREDICT = int(os.getenv("REASONING_NUM_PREDICT", "8192"))
_REASONING_THINK = os.getenv("REASONING_THINK", "medium")
_BUDGET_S = 600.0  # 10 минут на запрос: скорость не критична, thinking-модели думают долго


def _ollama_skip_reason() -> str | None:
    url = _detect_ollama_url()
    if not _is_url_reachable(url):
        return f"Ollama недоступна по {url}"
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as resp:
            tags = json.load(resp)
    except (OSError, ValueError) as e:
        return f"не удалось прочитать список моделей Ollama: {e}"
    names = {m.get("name", "") for m in tags.get("models", [])}
    base = _REASONING_MODEL.split(":", 1)[0]
    has_model = any(n == _REASONING_MODEL or n.split(":", 1)[0] == base for n in names)
    if not has_model:
        return f"модель {_REASONING_MODEL} не загружена в Ollama (есть: {sorted(names)})"
    return None


# Жёсткая языковая фиксация. Abliterated-модели (особенно Qwen) после удаления
# refusal direction склонны спонтанно переключаться на украинский/английский.
# Тройная фиксация: инструкция в system + дубль в конце user + пример первых слов ответа.
LANGUAGE_LOCK_SYSTEM = (
    "КРИТИЧЕСКИ ВАЖНО: отвечай СТРОГО на русском языке. Не используй украинский, "
    "английский или другие языки ни в одном предложении. Медицинские термины пиши "
    "по-русски (допустимы латинские аббревиатуры TSH, HRV, T4). "
    "Перед отправкой проверь: весь текст — только русский.\n\n"
)
LANGUAGE_LOCK_USER_SUFFIX = "\n\nВажно: ответ дай полностью на русском языке."


def _call_ollama(
    prompt: str,
    system: str = "",
    timeout: int = 600,
    *,
    think: str | bool | None = None,
) -> tuple[str, float]:
    """Вызов Ollama с контролем thinking и fallback для пустого content.

    Ollama возвращает reasoning отдельно от финального ответа. Если модель потратила
    весь лимит на thinking и оставила content пустым, повторяем запрос с think=false:
    пользовательский ответ важнее непоказанного reasoning-трейса.
    """
    url = _detect_ollama_url()
    selected_think = _normalize_think(_REASONING_THINK if think is None else think)
    payload = {
        "model": _REASONING_MODEL,
        "messages": [],
        "stream": False,
        "think": selected_think,
        "options": {
            "temperature": 0.1,
            "num_ctx": 16384,
            "num_predict": _REASONING_NUM_PREDICT,
        },
    }
    full_system = LANGUAGE_LOCK_SYSTEM + system if system else LANGUAGE_LOCK_SYSTEM.strip()
    payload["messages"].append({"role": "system", "content": full_system})
    payload["messages"].append({"role": "user", "content": prompt + LANGUAGE_LOCK_USER_SUFFIX})

    start = time.monotonic()
    result = _request_ollama(url, payload, timeout)
    text = (result.get("message", {}).get("content") or "").strip()
    thinking = result.get("message", {}).get("thinking") or ""
    print(
        f"\n[OLLAMA] think={selected_think!r} content={len(text)} "
        f"thinking={len(thinking)} done={result.get('done_reason', '?')}"
    )
    if not text and selected_think is not False:
        fallback = dict(payload)
        fallback["think"] = False
        result = _request_ollama(url, fallback, timeout)
        text = (result.get("message", {}).get("content") or "").strip()
        print(f"[OLLAMA] fallback think=False content={len(text)}")
    elapsed = time.monotonic() - start
    print(f"[LANG] {_language_profile(text)}")
    return text, elapsed


def _normalize_think(value: str | bool) -> str | bool:
    """Преобразует env-значение thinking в тип, который принимает Ollama API."""
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"false", "0", "off", "no"}:
        return False
    if normalized in {"true", "1", "on", "yes"}:
        return True
    if normalized in {"low", "medium", "high"}:
        return normalized
    raise ValueError(f"REASONING_THINK должен быть false/true/low/medium/high, получено: {value!r}")


def _request_ollama(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _is_russian_response(text: str) -> bool:
    """Проверяет русский текст с небольшим допуском для медицинских аббревиатур."""
    if not text:
        return False
    ua_chars = sum(text.count(character) for character in "іїєґІЇЄҐ")
    cyr_chars = sum(1 for character in text if "\u0400" <= character <= "\u04FF")
    lat_chars = sum(1 for character in text if character.isascii() and character.isalpha())
    return ua_chars == 0 and cyr_chars >= max(20, lat_chars * 4)


def _language_profile(text: str) -> str:
    """Быстрый профиль языка ответа: доля украинских маркеров и латиницы.

    Украинские буквы і/ї/є/ґ не встречаются в русском — их наличие в кириллическом
    тексте однозначно указывает на украинизацию. Метрика нужна для сравнения
    промптов до/после языковой фиксации.
    """
    if not text:
        return "empty"
    ua_chars = sum(text.count(c) for c in "іїєґІЇЄҐ")
    cyr_chars = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    lat_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    letters = cyr_chars + lat_chars
    if letters == 0:
        return "no-letters"
    ua_ratio = ua_chars / cyr_chars if cyr_chars else 0.0
    lat_ratio = lat_chars / letters
    verdict = "RU"
    if ua_ratio > 0.005:
        verdict = "UA-mixed" if ua_ratio < 0.05 else "UA"
    elif lat_ratio > 0.5:
        verdict = "EN"
    return f"{verdict} (ua_ratio={ua_ratio:.4f}, lat_ratio={lat_ratio:.2f}, cyr={cyr_chars}, lat={lat_chars})"


# --- Тестовые данные ---

LAB_RESULTS_JSON = json.dumps([
    {"parameter": "Гемоглобин", "value": "92", "unit": "г/л", "reference_range": "120-150", "flag": "↓"},
    {"parameter": "Ферритин", "value": "8", "unit": "мкг/л", "reference_range": "15-150", "flag": "↓"},
    {"parameter": "Железо сыворотки", "value": "7.2", "unit": "мкмоль/л", "reference_range": "9.0-30.4", "flag": "↓"},
    {"parameter": "TSH", "value": "6.8", "unit": "мкМЕ/мл", "reference_range": "0.4-4.0", "flag": "↑"},
    {"parameter": "T4 свободный", "value": "9.1", "unit": "пмоль/л", "reference_range": "10.0-22.0", "flag": "↓"},
    {"parameter": "Витамин D", "value": "18", "unit": "нг/мл", "reference_range": "30-100", "flag": "↓"},
    {"parameter": "Глюкоза", "value": "5.4", "unit": "ммоль/л", "reference_range": "3.9-6.1", "flag": None},
    {"parameter": "Лейкоциты", "value": "6.2", "unit": "10^9/л", "reference_range": "4.0-9.0", "flag": None},
], ensure_ascii=False)

GARMIN_DATA_JSON = json.dumps({
    "resting_hr": 78,
    "average_hr_7d": 82,
    "sleep_duration_hours": 5.2,
    "deep_sleep_pct": 12,
    "rem_sleep_pct": 15,
    "stress_score_avg": 55,
    "steps_7d_avg": 4200,
    "vo2max": 32,
    "body_battery_max": 45,
    "hrv_avg_ms": 28,
}, ensure_ascii=False)

DOCTOR_PRESCRIPTION = """
Диагноз: Астения, анемия лёгкой степени.
Назначения:
1. Сорбифер 1 таб/день — 3 месяца
2. L-тироксин 25 мкг/день
3. Витамин D 2000 МЕ/день
Рекомендации: контроль ОАК через 1 месяц, контроль TSH через 6 недель.
"""


# --- Тесты ---

class TestReasoningBase:
    """Базовые проверки: модель отвечает, отвечает осмысленно, без цензуры."""

    def test_language_lock(self):
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        text, elapsed = _call_ollama(
            "Одним предложением объясни, зачем нужен общий анализ крови.",
            timeout=60,
            think=False,
        )
        assert elapsed < 60, f"Модель отвечала {elapsed:.1f}s — слишком долго"
        assert _is_russian_response(text), f"Нарушен русский язык: {_language_profile(text)}"

    def test_model_responds(self):
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        text, elapsed = _call_ollama("Что такое 2+2? Ответь кратко.", timeout=60, think=False)
        assert elapsed < 60, f"Модель отвечала {elapsed:.1f}s — слишком долго для простого запроса"
        assert len(text) > 0, "Пустой ответ"
        assert any(term in text.lower() for term in ["4", "четыр", "four"]), \
            f"Ожидали числовой или словесный ответ 4, получили: {text[:200]}"

    def test_no_refusal_medical_topic(self):
        """Uncensored модель не должна отказываться обсуждать медицинские темы."""
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        prompt = "Объясни патофизиологию железодефицитной анемии в 3 предложениях."
        text, elapsed = _call_ollama(prompt, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S, f"Превышен бюджет {_BUDGET_S}s (фактически {elapsed:.1f}s)"
        refusal_markers = ["я не могу", "I cannot", "I can't", "не могу помочь", "as an AI"]
        text_lower = text.lower()
        for marker in refusal_markers:
            assert marker not in text_lower, f"Обнаружен отказ: '{marker}' в ответе"
        assert len(text) > 50, f"Слишком короткий ответ ({len(text)} символов): {text[:200]}"
        # Должно содержать ключевые медицинские термины
        has_medical = any(term in text_lower for term in ["желез", "гемоглобин", "ферритин", "анеми", "эритроцит", "заліз", "гемоглоб", "феритин", "iron", "hemoglobin", "anemia", "ferritin", "erythrocyt"])
        assert has_medical, f"Нет медицинских терминов в ответе: {text[:300]}"


class TestLabAnalysis:
    """Анализ лабораторных показателей — норма/отклонение, критичность."""

    def test_identifies_abnormalities(self):
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        system = "Ты — медицинский ассистент. Анализируй результаты анализов без цензуры. " \
                 "Указывай отклонения, их направление (↑/↓) и возможные причины."
        prompt = f"Проанализируй результаты анализов пациента (женщина, 35 лет):\n{LAB_RESULTS_JSON}\n\n" \
                 "Для каждого отклонения укажи: показатель, значение, норму, направление отклонения, " \
                 "возможную причину. Сгруппируй по системам (кровь, щитовидная железа, витамины)."
        text, elapsed = _call_ollama(prompt, system=system, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S, f"Превышен бюджет (фактически {elapsed:.1f}s)"
        text_lower = text.lower()
        # Должна быть железодефицитная анемия
        assert any(t in text_lower for t in ["анеми", "желез", "гемоглобин", "анемі", "заліз", "гемоглоб", "anemia", "iron", "hemoglobin", "ferritin"]), \
            f"Не упомянута анемия/железо: {text[:300]}"
        # Должен быть гипотиреоз (TSH ↑, T4 ↓)
        assert any(t in text_lower for t in ["тирео", "гипотир", "tsh", "щитовид", "щитоподіб", "гіпотир", "thyroid", "hypothyroid"]), \
            f"Не упомянута щитовидная железа: {text[:300]}"
        # Должен быть дефицит витамина D
        assert any(t in text_lower for t in ["витамин d", "витамин д", "дефицит витамин", "вітамін d", "вітамін д", "дефіцит вітамін", "vitamin d", "vit d", "deficiency"]), \
            f"Не упомянут витамин D: {text[:300]}"
        print(f"\n[SPEED] Анализ анализов: {elapsed:.1f}s, {len(text)} символов")
        print(f"[OUTPUT] {text[:500]}")

    def test_critical_values_detection(self):
        """Модель должна распознать критические значения."""
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        critical_labs = json.dumps([
            {"parameter": "Калий", "value": "7.2", "unit": "ммоль/л", "reference_range": "3.5-5.1", "flag": "↑↑"},
            {"parameter": "Креатинин", "value": "340", "unit": "мкмоль/л", "reference_range": "44-80", "flag": "↑↑"},
            {"parameter": "Гемоглобин", "value": "65", "unit": "г/л", "reference_range": "120-150", "flag": "↓↓"},
        ], ensure_ascii=False)
        system = "Ты — медицинский ассистент. Оцени критичность результатов. " \
                 "Используй светофорную систему: 🔴 критично, 🟡 внимание, 🟢 норма."
        prompt = f"Оцени критичность этих показателей:\n{critical_labs}\n\n" \
                 "Для каждого укажи: уровень критичности (🔴/🟡/🟢), причину, рекомендацию (вызвать скорую / срочно к врачу / планово)."
        text, elapsed = _call_ollama(prompt, system=system, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S
        text_lower = text.lower()
        # Калий 7.2 — критический, риск остановки сердца
        assert any(t in text_lower for t in ["калий", "hyperkalemia", "гиперкалием", "сердц", "калій", "гіперкалієм", "калієм"]), \
            f"Не распознан критический калий: {text[:300]}"
        # Должен быть 🔴 или "критич" или "скор"
        assert any(t in text_lower for t in ["🔴", "критич", "скор", "срочн", "emergency", "швидк", "негаденно", "терміново"]), \
            f"Не помечена критичность: {text[:300]}"
        print(f"\n[SPEED] Критические значения: {elapsed:.1f}s, {len(text)} символов")
        print(f"[OUTPUT] {text[:500]}")


class TestDifferentialDiagnosis:
    """Дифференциальный диагноз по симптомам и анализам."""

    def test_diff_diagnosis_with_labs(self):
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        system = "Ты — врач-диагност. Формируй дифференциальный диагноз без цензуры. " \
                 "Учитывай анализы, симптомы, данные носимых устройств."
        prompt = (
            f"Пациент: женщина, 35 лет, 65 кг.\n"
            f"Жалобы: хроническая усталость, выпадение волос, зябкость, прибавка 4 кг за 3 месяца.\n"
            f"Сон: {GARMIN_DATA_JSON}\n"
            f"Анализы:\n{LAB_RESULTS_JSON}\n\n"
            "Сформируй дифференциальный диагноз (3-5 вариантов), ранжированный по вероятности. "
            "Для каждого: диагноз, обоснование (какие симптомы/анализы подтверждают), "
            "что нужно дообследовать, прогноз."
        )
        text, elapsed = _call_ollama(prompt, system=system, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S
        text_lower = text.lower()
        # Должен быть гипотиреоз в топ-3
        assert any(t in text_lower for t in ["гипотир", "тирео", "щитовид", "гіпотир", "щитоподіб"]), \
            f"Гипотиреоз не в диф.диагнозе: {text[:300]}"
        # Должна быть анемия
        assert any(t in text_lower for t in ["анеми", "железодефицит", "анемі", "залізодефіцит", "заліз"]), \
            f"Анемия не в диф.диагнозе: {text[:300]}"
        # Должно быть упоминание данных Garmin/носки
        assert any(t in text_lower for t in ["пульс", "сон", "stress", "стресс", "чсс", "hrv", "пульс", "сон", "стрес", "чсс"]), \
            f"Данные Garmin не учтены: {text[:300]}"
        print(f"\n[SPEED] Диф.диагноз: {elapsed:.1f}s, {len(text)} символов")
        print(f"[OUTPUT] {text[:500]}")


class TestTreatmentStrategy:
    """Стратегия лечения с учётом данных анализов и Garmin."""

    def test_treatment_recommendations(self):
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        system = "Ты — медицинский консультант. Формируй стратегию лечения без цензуры. " \
                 "Учитывай анализы, данные носимых устройств, образ жизни. " \
                 "Структурируй: диагностика → лечение → мониторинг → образ жизни."
        prompt = (
            f"Пациент: женщина, 35 лет.\n"
            f"Анализы:\n{LAB_RESULTS_JSON}\n"
            f"Данные Garmin (за 7 дней):\n{GARMIN_DATA_JSON}\n\n"
            "Сформируй стратегию лечения:\n"
            "1. Какие препараты и дозы (с обоснованием)\n"
            "2. Контрольные анализы (что и когда)\n"
            "3. Рекомендации по образу жизни (сон, активность, питание)\n"
            "4. Что отслеживать через Garmin (показатели и целевые значения)\n"
            "5. Когда к врачу и к какому"
        )
        text, elapsed = _call_ollama(prompt, system=system, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S
        text_lower = text.lower()
        # Должны быть конкретные препараты
        assert any(t in text_lower for t in ["сорбифер", "железо", "тироксин", "l-тироксин", "левотироксин", "витамин d", "сорбіфер", "залізо", "тироксин", "л-тироксин", "левотироксин", "вітамін d"]), \
            f"Нет конкретных препаратов: {text[:300]}"
        # Должны быть контрольные анализы
        assert any(t in text_lower for t in ["контроль", "анализ", "провер", "мониторинг", "контрол", "аналіз", "перевір", "моніторинг"]), \
            f"Нет контроля анализов: {text[:300]}"
        # Должны быть рекомендации по Garmin
        assert any(t in text_lower for t in ["garmin", "пульс", "сон", "шаг", "hrv", "стресс", "крок", "стрес"]), \
            f"Нет рекомендаций по Garmin: {text[:300]}"
        print(f"\n[SPEED] Стратегия лечения: {elapsed:.1f}s, {len(text)} символов")
        print(f"[OUTPUT] {text[:500]}")


class TestDoctorVerification:
    """Проверка назначений врача на соответствие стандартам."""

    def test_verify_doctor_prescription(self):
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        system = "Ты — медицинский аудитор. Проверяй назначения врача на соответствие " \
                 "современным клиническим рекомендациям. Без цензуры — указывай и ошибки, и упущения."
        prompt = (
            f"Пациент: женщина, 35 лет.\n"
            f"Анализы:\n{LAB_RESULTS_JSON}\n"
            f"Данные Garmin:\n{GARMIN_DATA_JSON}\n\n"
            f"Назначения врача:\n{DOCTOR_PRESCRIPTION}\n\n"
            "Проверь назначения:\n"
            "1. Соответствуют ли диагнозы результатам анализов?\n"
            "2. Правильные ли препараты и дозы?\n"
            "3. Что упущено (что врач НЕ назначил, но нужно было)?\n"
            "4. Есть ли противопоказания с учётом данных Garmin?\n"
            "5. Оценка назначений: 1-10 с обоснованием"
        )
        text, elapsed = _call_ollama(prompt, system=system, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S
        text_lower = text.lower()
        # Должна быть оценка назначений
        assert any(t in text_lower for t in ["оценк", "балл", "1-10", "/10", "оцен", "score", "оцінк", "бал", "rating", "rate"]), \
            f"Нет оценки назначений: {text[:300]}"
        # Должно быть упоминание TSH/тироксина
        assert any(t in text_lower for t in ["тироксин", "tsh", "тирео", "щитовид", "щитоподіб"]), \
            f"Не проверен тироксин: {text[:300]}"
        # Должны быть упущения
        assert any(t in text_lower for t in ["упущ", "не назнач", "отсутств", "пропущ", "добавить", "следовало бы", "пропущен", "не признач", "відсутн", "пропущ", "додати", "слід було б", "missing", "not prescribed", "should have", "omitted"]), \
            f"Не указаны упущения: {text[:300]}"
        print(f"\n[SPEED] Проверка врача: {elapsed:.1f}s, {len(text)} символов")
        print(f"[OUTPUT] {text[:500]}")


class TestGarminIntegration:
    """Интеграция данных Garmin в медицинский анализ."""

    def test_garmin_anomaly_correlation(self):
        """Корреляция данных Garmin с анализами."""
        skip = _ollama_skip_reason()
        if skip:
            pytest.skip(skip)
        system = "Ты — медицинский аналитик. Сопоставляй данные носимых устройств с " \
                 "лабораторными показателями. Ищи корреляции и аномалии."
        prompt = (
            f"Пациент: женщина, 35 лет.\n"
            f"Анализы:\n{LAB_RESULTS_JSON}\n"
            f"Данные Garmin (7 дней):\n{GARMIN_DATA_JSON}\n\n"
            "Найди корреляции между данными Garmin и анализами:\n"
            "1. Какие показатели Garmin аномальны и почему?\n"
            "2. Как эти аномалии связаны с лабораторными отклонениями?\n"
            "3. Что рекомендовать отслеживать дополнительно?\n"
            "4. Какие целевые показатели Garmin поставить на 1/3/6 месяцев?"
        )
        text, elapsed = _call_ollama(prompt, system=system, timeout=_BUDGET_S)
        assert elapsed < _BUDGET_S
        text_lower = text.lower()
        # Высокий пульс покоя (78) + низкий HRV (28) → связь с гипотиреозом/анемией
        assert any(t in text_lower for t in ["пульс", "чсс", "hr", "heart rate", "пульс", "heart rate", "чсс", "bpm", "pulse", "cardiac"]), \
            f"Не проанализирован пульс: {text[:300]}"
        assert any(t in text_lower for t in ["сон", "sleep", "deep", "сон", "deep", "глибок", "rem", "deep sleep"]), \
            f"Не проанализирован сон: {text[:300]}"
        assert any(t in text_lower for t in ["корреляц", "связ", "обусловлен", "причин", "кореляц", "зв'яз", "зумовлен", "причин", "correlat", "link", "related", "caused"]), \
            f"Не показаны корреляции: {text[:300]}"
        print(f"\n[SPEED] Garmin интеграция: {elapsed:.1f}s, {len(text)} символов")
        print(f"[OUTPUT] {text[:500]}")
