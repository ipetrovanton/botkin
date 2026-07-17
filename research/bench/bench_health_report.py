"""Сравнительный бенчмарк моделей на комплексном отчёте о здоровье пациента.

Скрипт:
1. Выгружает и дедуплицирует все данные пациента (user_id=1) из БД:
   - лабораторные результаты с референсами
   - заключения врачей (диагнозы, лекарства, рекомендации)
   - метрики Garmin (пульс, сон, стресс, HRV, шаги, body battery)
   - активности Garmin (бег, плавание, велосипед, ходьба)
2. Подтягивает RAG-справочники (ГРЛС/ФСЛИ) и исследования PubMed по диагнозам
3. Собирает структурированный промт для комплексного отчёта
4. Прогоняет каждую модель по очереди (с выгрузкой предыдущей из VRAM)
5. Замеряет: wall time, токены, объём выхода, язык, цензуру, контекст
6. Сохраняет сырые выходы и метрики в habr/bench-health-report/
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from botkin.clinical.facts import parse_reference_range

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "habr" / "bench-health-report"
DB_PATH = PROJECT_ROOT / "data" / "botkin.db"

DEFAULT_MODELS = [
    "huihui_ai/glm-4.7-flash-abliterated",
    "huihui_ai/Qwen3.6-abliterated:35b",
    "satgeze/qwen36-35b-uncensored-1m",
    "huihui_ai/Qwen3.6-abliterated:27b",
    "gemma4:latest",
    "goekdenizguelmez/JOSIEFIED-Qwen3:8b-health",
]

MODEL_CONFIGS: dict[str, dict] = {
    "huihui_ai/glm-4.7-flash-abliterated": {
        "think": "medium", "num_ctx": 65536, "num_predict": 24576,
        "temperature": 0.4, "top_p": 0.9, "seed": 42,
    },
    "huihui_ai/Qwen3.6-abliterated:35b": {
        "think": "high", "num_ctx": 65536, "num_predict": 24576,
        "temperature": 0.4, "top_p": 0.9, "seed": 42,
    },
    "satgeze/qwen36-35b-uncensored-1m": {
        "think": "high", "num_ctx": 65536, "num_predict": 24576,
        "temperature": 0.4, "top_p": 0.9, "seed": 42,
    },
    "huihui_ai/Qwen3.6-abliterated:27b": {
        "think": "high", "num_ctx": 65536, "num_predict": 24576,
        "temperature": 0.4, "top_p": 0.9, "seed": 42,
    },
    "gemma4:latest": {
        "think": False, "num_ctx": 32768, "num_predict": 8192,
        "temperature": 0.4, "top_p": 0.9, "seed": 42,
    },
    "goekdenizguelmez/JOSIEFIED-Qwen3:8b-health": {
        "think": "medium", "num_ctx": 32768, "num_predict": 8192,
        "temperature": 0.4, "top_p": 0.9, "seed": 42,
    },
}


@dataclass
class ModelMetrics:
    model: str
    wall_s: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    output_chars: int = 0
    output_words: int = 0
    thinking_chars: int = 0
    done_reason: str = ""
    content_empty: bool = False
    fallback_used: bool = False
    language: str = ""
    ua_ratio: float = 0.0
    lat_ratio: float = 0.0
    has_refusal: bool = False
    section_count: int = 0
    references_research: bool = False
    references_garmin: bool = False
    references_labs: bool = False
    references_meds: bool = False
    error: str | None = None
    raw_output: str = ""
    raw_thinking: str = ""


def _detect_ollama_url() -> str:
    return os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def _stop_all_models():
    """Выгружает все модели из VRAM."""
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().splitlines()[1:]
        for line in lines:
            parts = line.split()
            if parts:
                model_name = parts[0]
                subprocess.run(
                    ["ollama", "stop", model_name],
                    capture_output=True, timeout=30, check=False,
                )
                print(f"  [STOP] {model_name}", flush=True)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        print(f"  [STOP] предупреждение: {exc}", flush=True)


def _stream_ollama(url: str, payload: dict, timeout: int) -> dict:
    """Стриминг-вызов Ollama /api/chat с живым выводом в консоль.

    Возвращает финальный агрегированный result (как при stream=False).
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )

    content_parts: list[str] = []
    thinking_parts: list[str] = []
    final_result: dict = {}
    eval_count = 0
    prompt_eval_count = 0
    last_heartbeat = 0.0
    start = time.monotonic()
    phase = "prompt"  # prompt → thinking → content → done

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            chunk = json.loads(line)
            done = chunk.get("done", False)

            if done:
                final_result = chunk
                phase = "done"
                break

            msg = chunk.get("message", {})
            token = msg.get("content") or ""
            think_token = msg.get("thinking") or ""

            if think_token:
                if phase == "prompt":
                    phase = "thinking"
                    print("  [THINK] старт reasoning...", flush=True)
                thinking_parts.append(think_token)

            if token:
                if phase in ("prompt", "thinking"):
                    phase = "content"
                    elapsed_now = time.monotonic() - start
                    print(f"  [GEN] старт генерации ответа "
                          f"(thinking: {len(''.join(thinking_parts))} символов, "
                          f"{elapsed_now:.0f}s)", flush=True)
                content_parts.append(token)

            eval_count = chunk.get("eval_count", eval_count)
            prompt_eval_count = chunk.get("prompt_eval_count", prompt_eval_count)

            # Heartbeat каждые 5 секунд
            elapsed_now = time.monotonic() - start
            if elapsed_now - last_heartbeat >= 5.0:
                last_heartbeat = elapsed_now
                content_len = len("".join(content_parts))
                think_len = len("".join(thinking_parts))
                phase_label = {"prompt": "обработка промта",
                               "thinking": "размышление",
                               "content": "генерация",
                               "done": "завершено"}.get(phase, phase)
                print(f"  [{elapsed_now:6.0f}s] {phase_label}: "
                      f"content={content_len} симв, "
                      f"thinking={think_len} симв, "
                      f"tokens={eval_count}", flush=True)

    # Если финальный chunk не пришёл с done=True, собираем вручную
    if not final_result:
        final_result = {
            "message": {
                "content": "".join(content_parts),
                "thinking": "".join(thinking_parts),
            },
            "eval_count": eval_count,
            "prompt_eval_count": prompt_eval_count,
            "done_reason": "stream-end",
        }
    else:
        # Гарантируем, что content/thinking собраны полностью
        msg = final_result.setdefault("message", {})
        if not msg.get("content"):
            msg["content"] = "".join(content_parts)
        if not msg.get("thinking"):
            msg["thinking"] = "".join(thinking_parts)

    elapsed_total = time.monotonic() - start
    content_total = len("".join(content_parts))
    think_total = len("".join(thinking_parts))
    print(f"  [FINAL] {elapsed_total:.0f}s, "
          f"content={content_total} симв, "
          f"thinking={think_total} симв, "
          f"tokens={eval_count}, "
          f"done={final_result.get('done_reason', 'n/a')}", flush=True)

    return final_result


def _call_ollama(model, system, user, config, timeout=1800):
    """Вызов Ollama со стримингом и fallback на think=false при пустом content."""
    url = _detect_ollama_url()
    think = config.get("think", False)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "think": think,
        "options": {
            "temperature": config.get("temperature", 0.4),
            "top_p": config.get("top_p", 0.9),
            "num_ctx": config.get("num_ctx", 32768),
            "num_predict": config.get("num_predict", 12288),
            "seed": config.get("seed", 42),
        },
    }
    start = time.monotonic()
    result = _stream_ollama(url, payload, timeout)
    content = (result.get("message", {}).get("content") or "").strip()
    fallback_used = False
    if not content and think is not False:
        print(f"  [FALLBACK] think={think!r} → пустой content, повтор с think=false", flush=True)
        fb = dict(payload)
        fb["think"] = False
        result = _stream_ollama(url, fb, timeout)
        content = (result.get("message", {}).get("content") or "").strip()
        fallback_used = True
    elapsed = time.monotonic() - start
    return result, elapsed, fallback_used


# --- Загрузка данных пациента ---

def _load_lab_results() -> list[dict]:
    """Дедуплицированные лабораторные результаты пользователя 1."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT
            COALESCE(analyte_canonical, analyte_name) AS name,
            analyte_name AS raw_name,
            value_num, value_text, unit, ref_low, ref_high,
            ref_operator, ref_text, taken_at,
            analyte_group, loinc
        FROM lab_results
        WHERE user_id = 1 AND value_num IS NOT NULL
        ORDER BY name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_doctor_reports() -> list[dict]:
    """Дедуплицированные заключения — по уникальному (visit_date, diagnosis[:80])."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT visit_date, doctor_name, department, diagnosis,
               medications_json, recommendations_json, complaints_json, anamnesis
        FROM doctor_reports
        WHERE user_id = 1
        ORDER BY visit_date
    """).fetchall()
    conn.close()
    seen: set[str] = set()
    unique: list[dict] = []
    for r in rows:
        row = dict(r)
        key = f"{row.get('visit_date') or ''}|{(row.get('diagnosis') or '')[:80]}"
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _load_garmin_daily() -> list[dict]:
    """Ежедневные метрики Garmin (без сырого пульса — только агрегаты)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT metric, ROUND(AVG(value_num), 1) AS avg_val,
               ROUND(MIN(value_num), 1) AS min_val,
               ROUND(MAX(value_num), 1) AS max_val,
               unit, COUNT(*) AS cnt,
               MIN(taken_at) AS first_date, MAX(taken_at) AS last_date
        FROM health_metrics
        WHERE user_id = 1 AND metric != 'heart_rate'
        GROUP BY metric ORDER BY cnt DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_garmin_activities() -> list[dict]:
    """Сводка активностей Garmin по типам."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT activity_type, COUNT(*) AS cnt,
               ROUND(AVG(duration_s) / 60, 1) AS avg_duration_min,
               ROUND(AVG(distance_m) / 1000, 2) AS avg_distance_km,
               ROUND(AVG(calories), 0) AS avg_calories,
               ROUND(AVG(avg_hr), 0) AS avg_hr,
               ROUND(AVG(max_hr), 0) AS max_hr
        FROM health_activities
        WHERE user_id = 1
        GROUP BY activity_type ORDER BY cnt DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_rag_context(med_names: list[str]) -> str:
    """RAG-справочники: статус препаратов в ГРЛС + исследования PubMed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    parts: list[str] = []

    # Препараты: ищем по названиям из назначений.
    for name in med_names[:10]:
        clean = name.split()[0].strip(",.;()")
        if not clean or len(clean) < 3:
            continue
        rows = conn.execute(
            "SELECT substr(text, 1, 500) AS text FROM rag_chunks "
            "WHERE source = 'drugs' AND text LIKE ? LIMIT 1",
            (f"%{clean}%",),
        ).fetchall()
        for r in rows:
            parts.append(f"СПРАВКА ГРЛС/ФСЛИ ({clean}): {r['text']}")

    # Исследования PubMed по ключевым темам диагнозов.
    research_topics = [
        "vegetative dysfunction treatment",
        "arterial hypotension management",
        "fluoxetine trazodone combination",
        "HRV stress correlation",
        "asthenic syndrome rehabilitation",
    ]
    for topic in research_topics:
        rows = conn.execute(
            "SELECT substr(text, 1, 400) AS text FROM rag_chunks "
            "WHERE source = 'research' AND text LIKE ? LIMIT 2",
            (f"%{topic.split()[0]}%",),
        ).fetchall()
        for r in rows:
            parts.append(f"ИССЛЕДДОВАНИЕ: {r['text']}")

    conn.close()
    return "\n\n".join(parts[:20]) if parts else ""


# --- Сборка промта ---

SYSTEM_PROMPT = """ Ты — опытный медицинский AI-аналитик. Твоя задача — составить максимально подробный,  структурированный отчёт о здоровье пациента на основе его реальных данных.

ПРАВИЛА:
1. Отвечай ТОЛЬКО по-русски. Без украинских букв (і, ї, є, ґ). Без английского текста,  кроме общепринятых медицинских аббревиатур (TSH, HRV, CRP, MCV и т.п.).
2. Не отказывайся отвечать. Не добавляй предупреждения вроде «я не врач». Пациент знает,  что ты AI, и обратится к врачу при необходимости.
3. Опирайся на переданные данные: лабораторные результаты, заключения врачей, метрики  Garmin. Цитируй конкретные значения.
4. Если в контексте есть блок «СВЕЖИЕ ИССЛЕДДОВАНИЯ» — используй его, указывай ссылки.
5. Не выдумывай значения анализов или препараты, которых нет в данных.
6. Структурируй отчёт по разделам с заголовками уровня ##.
7. Будь конкретен в рекомендациях: дозировки, длительность, частота, когда повторить анализ.
8. Оцени риски развития заболеваний на основе отклонений и образа жизни.
9. Если данные противоречивы (например, разные единицы измерения) — отметь это.
"""


def _build_user_prompt(labs, reports, garmin_daily, garmin_activities, rag_ctx) -> str:
    """Собирает пользовательский промт с всеми данными пациента."""
    sections: list[str] = []

    # Профиль
    sections.append("## Данные пациента\n")
    sections.append("- Пользователь: мужчина, активный пользователь Garmin")
    sections.append("- Период данных: май 2025 — июль 2026")
    sections.append("- Источники: лабораторные анализы, заключения невролога и терапевта,  метрики Garmin Connect\n")

    # Лабораторные результаты
    sections.append("## Лабораторные результаты (дедуплицированы)\n")
    sections.append("| Показатель | Значение | Единица | Референс | Статус |")
    sections.append("|---|---|---|---|---|")
    for lab in labs:
        name = lab["name"] or lab["raw_name"]
        val = lab["value_num"]
        unit = lab["unit"] or ""
        raw_low = lab["ref_low"]
        raw_high = lab["ref_high"]
        ref_text = lab["ref_text"] or ""
        # Флаг нормы считаем по числовым границам; если их нет — парсим из текста.
        low, high = raw_low, raw_high
        if low is None and high is None:
            low, high = parse_reference_range(ref_text)

        if raw_low is not None and raw_high is not None:
            ref = f"{raw_low:g}–{raw_high:g}"
        elif ref_text:
            ref = ref_text
        elif raw_low is not None:
            ref = f"от {raw_low:g}"
        elif raw_high is not None:
            ref = f"до {raw_high:g}"
        else:
            ref = "не указан"

        if low is not None and val < low:
            status = "↓ ниже нормы"
        elif high is not None and val > high:
            status = "↑ выше нормы"
        else:
            status = "в норме"
        sections.append(f"| {name} | {val:g} | {unit} | {ref} | {status} |")
    sections.append("")

    # Заключения врачей
    sections.append("## Заключения врачей (дедуплицированы)\n")
    for i, rep in enumerate(reports, 1):
        sections.append(f"### Заключение {i}")
        sections.append(f"- Дата: {rep['visit_date']}")
        sections.append(f"- Врач: {rep['doctor_name']}")
        if rep["department"]:
            sections.append(f"- Отделение: {rep['department']}")
        sections.append(f"- Диагноз: {rep['diagnosis']}")
        meds = rep.get("medications_json", "[]")
        try:
            med_list = json.loads(meds) if meds else []
        except json.JSONDecodeError:
            med_list = []
        if med_list:
            sections.append("- Лекарства:")
            for m in med_list:
                sections.append(f"  - {m}")
        recs = rep.get("recommendations_json", "[]")
        try:
            rec_list = json.loads(recs) if recs else []
        except json.JSONDecodeError:
            rec_list = []
        if rec_list:
            sections.append("- Рекомендации врача:")
            for r in rec_list:
                sections.append(f"  - {r}")
        sections.append("")

    # Garmin daily metrics
    sections.append("## Данные Garmin Connect (30 дней, июнь–июль 2026)\n")
    metric_labels = {
        "resting_heart_rate": "Пульс в покое",
        "steps": "Шаги в день",
        "sleep_seconds": "Сон (сек)",
        "stress_avg": "Средний стресс",
        "hrv_last_night": "HRV (ночь)",
        "body_battery_max": "Body Battery (макс)",
    }
    for g in garmin_daily:
        label = metric_labels.get(g["metric"], g["metric"])
        avg_val = g["avg_val"]
        if g["metric"] == "sleep_seconds":
            avg_val = f"{g['avg_val'] / 3600:.1f} ч"
            min_val = f"{g['min_val'] / 3600:.1f} ч"
            max_val = f"{g['max_val'] / 3600:.1f} ч"
        else:
            avg_val = f"{g['avg_val']}"
            min_val = f"{g['min_val']}"
            max_val = f"{g['max_val']}"
        sections.append(
            f"- {label}: средн {avg_val} {g['unit']}, "
            f"мин {min_val}, макс {max_val} "
            f"({g['cnt']} замеров, {g['first_date']} — {g['last_date']})"
        )
    sections.append("")

    # Garmin activities
    sections.append("## Активности Garmin (за период)\n")
    for a in garmin_activities:
        sections.append(
            f"- {a['activity_type']}: {a['cnt']} раз, "
            f"средн {a['avg_duration_min']} мин, "
            f"{a['avg_distance_km']} км, "
            f"{a['avg_calories']} ккал, "
            f"пульс {a['avg_hr']}/{a['max_hr']}"
        )
    sections.append("")

    # RAG context
    if rag_ctx:
        sections.append("## СВЕЖИЕ ИССЛЕДДОВАНИЯ И СПРАВКИ\n")
        sections.append(rag_ctx)
        sections.append("")

    # Запрос отчёта
    sections.append("## Задача\n")
    sections.append("Составь подробный структурированный отчёт о здоровье пациента.")
    sections.append("Включи следующие разделы:\n")
    sections.append("1. **Общая оценка здоровья** — сводный вывод на основе всех данных")
    sections.append("2. **Анализ лабораторных результатов** — отклонения, возможные причины,  динамика, что требует внимания")
    sections.append("3. **Анализ заключений врачей** — сопоставление диагнозов, адекватность  назначений, выявленные противоречия")
    sections.append("4. **Анализ данных Garmin** — качество сна, уровень стресса, HRV,   физическая активность, тренды")
    sections.append("5. **Оценка рисков** — риск развития заболеваний на основе отклонений и   образа жизни (сердечно-сосудистые, метаболические, психические)")
    sections.append("6. **Рекомендации по образу жизни** — сон, питание, физическая активность,   режим дня, стресс-менеджмент")
    sections.append("7. **Рекомендации по медикаментам** — оценка текущей схемы, возможные   корректировки, что обсудить с врачом")
    sections.append("8. **Рекомендации по обращению к врачам** — каких специалистов посетить,   какие исследования пройти, в какой срок")
    sections.append("9. **Рекомендации по питанию** — конкретные продукты, добавки, режим")
    sections.append("10. **Рекомендации по физическим нагрузкам** — тип, частота, интенсивность,   учитывая диагнозы и данные Garmin")
    sections.append("11. **Ускорение восстановления и качество жизни** — практические шаги")
    sections.append("12. **Итог** — главные выводы и приоритеты\n")
    sections.append("Каждый раздел должен содержать конкретные данные из контекста,")
    sections.append("а не общие фразы. Если данных недостаточно для вывода — укажи, чего не хватает.")

    return "\n".join(sections)


# --- Анализ выхода ---

def _analyze_output(text: str, thinking: str) -> dict:
    """Автоматические метрики качества выхода."""
    if not text:
        return {
            "language": "empty", "ua_ratio": 0.0, "lat_ratio": 0.0,
            "has_refusal": False, "section_count": 0,
            "references_research": False, "references_garmin": False,
            "references_labs": False, "references_meds": False,
        }
    ua_chars = sum(text.count(c) for c in "іїєґІЇЄҐ")
    cyr_chars = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    lat_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    letters = cyr_chars + lat_chars
    ua_ratio = ua_chars / cyr_chars if cyr_chars else 0.0
    lat_ratio = lat_chars / letters if letters else 0.0
    if ua_ratio > 0.005:
        language = "UA-mixed" if ua_ratio < 0.05 else "UA"
    elif lat_ratio > 0.5:
        language = "EN"
    else:
        language = "RU"

    refusal_markers = [
        "я не врач", "я не могу", "не могу дать рекомендаци",
        "обратитесь к врачу", "я не специалист", "не могу рекомендовать",
        "не имею права", "вне моей компетенции",
    ]
    has_refusal = any(m in text.lower() for m in refusal_markers)

    section_count = text.count("\n## ") + text.count("\n### ")

    references_research = any(
        kw in text.lower()
        for kw in ["исследован", "pubmed", "исследовани", "evidence", "данные исследований"]
    )
    references_garmin = any(
        kw in text.lower()
        for kw in ["garmin", "hrv", "body battery", "шаг", "сон", "стресс"]
    )
    references_labs = any(
        kw in text.lower()
        for kw in ["гемоглобин", "лейкоцит", "тромбоцит", "гематокрит", "эритроцит",
                    "соэ", "белок", "креатинин", "mcv", "mch", "rdw"]
    )
    references_meds = any(
        kw in text.lower()
        for kw in ["флюоксетин", "тритико", "глиатилин", "элькар", "адаптол",
                    "брейнмакс", "стимол", "бринтелликс"]
    )

    return {
        "language": language,
        "ua_ratio": round(ua_ratio, 4),
        "lat_ratio": round(lat_ratio, 2),
        "has_refusal": has_refusal,
        "section_count": section_count,
        "references_research": references_research,
        "references_garmin": references_garmin,
        "references_labs": references_labs,
        "references_meds": references_meds,
    }


def _run_model(model: str, system: str, user: str, config: dict) -> ModelMetrics:
    """Прогон одной модели с замерами."""
    metrics = ModelMetrics(model=model)
    print(f"\n{'='*70}")
    print(f"[MODEL] {model}")
    print(f"[CONFIG] think={config.get('think')}, "
          f"num_ctx={config.get('num_ctx')}, "
          f"num_predict={config.get('num_predict')}")
    print(f"{'='*70}", flush=True)

    try:
        result, elapsed, fallback = _call_ollama(model, system, user, config)
        metrics.wall_s = round(elapsed, 1)
        metrics.fallback_used = fallback

        msg = result.get("message", {})
        content = (msg.get("content") or "").strip()
        thinking = (msg.get("thinking") or "").strip()
        metrics.raw_output = content
        metrics.raw_thinking = thinking
        metrics.content_empty = not content
        metrics.done_reason = result.get("done_reason", "")

        # Токены
        prompt_eval = result.get("prompt_eval_count", 0)
        eval_count = result.get("eval_count", 0)
        metrics.prompt_tokens = prompt_eval
        metrics.output_tokens = eval_count

        # Объём
        metrics.output_chars = len(content)
        metrics.output_words = len(content.split())
        metrics.thinking_chars = len(thinking)

        # Анализ
        analysis = _analyze_output(content, thinking)
        metrics.language = analysis["language"]
        metrics.ua_ratio = analysis["ua_ratio"]
        metrics.lat_ratio = analysis["lat_ratio"]
        metrics.has_refusal = analysis["has_refusal"]
        metrics.section_count = analysis["section_count"]
        metrics.references_research = analysis["references_research"]
        metrics.references_garmin = analysis["references_garmin"]
        metrics.references_labs = analysis["references_labs"]
        metrics.references_meds = analysis["references_meds"]

        print(f"  [DONE] wall={metrics.wall_s}s, "
              f"tokens={metrics.output_tokens}, "
              f"chars={metrics.output_chars}, "
              f"lang={metrics.language}, "
              f"sections={metrics.section_count}", flush=True)
        if fallback:
            print("  [FALLBACK] использован повтор с think=false", flush=True)
        if metrics.content_empty:
            print("  [WARNING] пустой content!", flush=True)
        if metrics.has_refusal:
            print("  [WARNING] обнаружен отказ!", flush=True)

    except Exception as exc:
        metrics.error = str(exc)
        print(f"  [ERROR] {exc}", flush=True)

    return metrics


def _print_comparison(results: list[ModelMetrics]) -> None:
    """Сравнительная таблица."""
    print(f"\n{'='*120}")
    print("СРАВНИТЕЛЬНАЯ ТАБЛИЦА КОМПЛЕКСНОГО ОТЧЁТА О ЗДОРОВЬЕ")
    print(f"{'='*120}")
    header = (
        f"{'Модель':<45} {'Wall':>7} {'Tok':>6} {'Chars':>6} "
        f"{'Lang':>8} {'Sect':>5} {'Refusal':>8} "
        f"{'Labs':>5} {'Garmin':>7} {'Meds':>5} {'Resrch':>7}"
    )
    print(header)
    print("-" * 120)
    for m in sorted(results, key=lambda x: x.output_chars, reverse=True):
        print(
            f"{m.model[:44]:<45} {m.wall_s:>6.0f}s {m.output_tokens:>6} "
            f"{m.output_chars:>6} {m.language:>8} {m.section_count:>5} "
            f"{'YES' if m.has_refusal else 'no':>8} "
            f"{'yes' if m.references_labs else 'no':>5} "
            f"{'yes' if m.references_garmin else 'no':>7} "
            f"{'yes' if m.references_meds else 'no':>5} "
            f"{'yes' if m.references_research else 'no':>7}"
        )
    print("=" * 120)


def main() -> int:
    parser = argparse.ArgumentParser(description="Бенчмарк комплексного отчёта о здоровье")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--timeout", type=int, default=1800, help="Таймаут на модель (с)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Загрузка данных пациента...")
    labs = _load_lab_results()
    reports = _load_doctor_reports()
    garmin_daily = _load_garmin_daily()
    garmin_activities = _load_garmin_activities()

    # Сбор названий лекарств для RAG
    med_names: list[str] = []
    for rep in reports:
        try:
            meds = json.loads(rep.get("medications_json") or "[]")
            med_names.extend(meds)
        except json.JSONDecodeError:
            pass
    rag_ctx = _load_rag_context(med_names)

    user_prompt = _build_user_prompt(labs, reports, garmin_daily, garmin_activities, rag_ctx)
    print(f"Дано: {len(labs)} анализов, {len(reports)} заключений, "
          f"{len(garmin_daily)} метрик Garmin, {len(garmin_activities)} типов активностей")
    print(f"Промт: {len(user_prompt)} символов")

    # Сохраняем промт
    (OUTPUT_DIR / "prompt_system.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
    (OUTPUT_DIR / "prompt_user.txt").write_text(user_prompt, encoding="utf-8")

    results: list[ModelMetrics] = []
    for model in args.models:
        config = MODEL_CONFIGS.get(model, {"think": False, "num_predict": 8192})
        _stop_all_models()
        metrics = _run_model(model, SYSTEM_PROMPT, user_prompt, config, )
        results.append(metrics)

        # Сохраняем сырой выход
        safe_name = model.replace("/", "_").replace(":", "_")
        (OUTPUT_DIR / f"output_{safe_name}.md").write_text(
            metrics.raw_output or "[ПУСТО]", encoding="utf-8",
        )
        if metrics.raw_thinking:
            (OUTPUT_DIR / f"thinking_{safe_name}.md").write_text(
                metrics.raw_thinking, encoding="utf-8",
            )

    _print_comparison(results)

    # Сохраняем метрики в JSON
    data = []
    for m in results:
        data.append({
            "model": m.model,
            "wall_s": m.wall_s,
            "prompt_tokens": m.prompt_tokens,
            "output_tokens": m.output_tokens,
            "output_chars": m.output_chars,
            "output_words": m.output_words,
            "thinking_chars": m.thinking_chars,
            "done_reason": m.done_reason,
            "content_empty": m.content_empty,
            "fallback_used": m.fallback_used,
            "language": m.language,
            "ua_ratio": m.ua_ratio,
            "lat_ratio": m.lat_ratio,
            "has_refusal": m.has_refusal,
            "section_count": m.section_count,
            "references_research": m.references_research,
            "references_garmin": m.references_garmin,
            "references_labs": m.references_labs,
            "references_meds": m.references_meds,
            "error": m.error,
        })
    (OUTPUT_DIR / "metrics.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nРезультаты сохранены: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
