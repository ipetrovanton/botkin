"""Сборка человекочитаемого контекста пациента для RAG-рекомендаций.

Контекст собирается из трёх источников:
1. Профиль пациента из БД — свежие отклонения анализов, назначенные лекарства.
2. Заключения врачей и лабораторные отклонения.
3. Данные носимых устройств за последние 2 недели (агрегаты) и внешние факторы.

Модуль не зависит от конкретной RAG-задачи: вызывается и из `recommend`,
и из `recommend_lifestyle`.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3

from botkin.clinical.facts import build_lab_facts, render_lab_facts
from botkin.config import (
    EXT_ASTROLOGY_ENABLED,
    EXT_DEFAULT_LAT,
    EXT_DEFAULT_LON,
    EXT_GEOMAGNETIC_ENABLED,
    EXT_WEATHER_ENABLED,
)
from botkin.db.connection import get_conn
from botkin.db.repos import HealthRepo, PatientRepo
from botkin.external import astrology, weather

log = logging.getLogger(__name__)

_RECENT_LABS_SQL = """
    SELECT COALESCE(analyte_canonical, analyte_name) AS name, value_num, unit,
           ref_low, ref_high, ref_text, taken_at
    FROM lab_results
    WHERE user_id = ? AND value_num IS NOT NULL
      AND (ref_low IS NOT NULL OR ref_high IS NOT NULL)
      AND (value_num < COALESCE(ref_low, -1e18) OR value_num > COALESCE(ref_high, 1e18))
    ORDER BY taken_at DESC LIMIT 15
"""

_RECENT_REPORTS_SQL = """
    SELECT diagnosis, recommendations_json, visit_date, doctor_name, department
    FROM doctor_reports
    WHERE user_id = ? AND (diagnosis IS NOT NULL OR recommendations_json IS NOT NULL)
    ORDER BY visit_date DESC LIMIT 5
"""


def _profile_context(conn: sqlite3.Connection, user_id: int) -> str | None:
    """Формы пациента (этап 4): профиль тела, текущие препараты, свежие жалобы.

    Возраст вычисляется из birth_date на момент запроса — хранимый «возраст» устаревает."""
    repo = PatientRepo(conn, user_id)
    profile = repo.get_profile()
    meds = repo.list_medications(active_only=True)
    complaints = repo.list_complaints(limit=5)
    if not profile and not meds and not complaints:
        return None
    lines = ["Профиль пациента (заполнен им самим):"]
    if profile:
        sex_ru = {"male": "мужской", "female": "женский"}.get(profile.get("sex") or "")
        if sex_ru:
            lines.append(f"- Пол: {sex_ru}")
        if profile.get("birth_date"):
            try:
                born = dt.date.fromisoformat(profile["birth_date"])
                today = dt.date.today()
                age = today.year - born.year - (
                    (today.month, today.day) < (born.month, born.day)
                )
                lines.append(f"- Возраст: {age}")
            except ValueError:
                pass
        if profile.get("height_cm"):
            lines.append(f"- Рост: {profile['height_cm']:g} см")
        if profile.get("weight_kg"):
            lines.append(f"- Вес: {profile['weight_kg']:g} кг")
        if profile.get("blood_type"):
            lines.append(f"- Группа крови: {profile['blood_type']}")
        if profile.get("allergies"):
            lines.append(f"- Аллергии: {profile['allergies']}")
        if profile.get("chronic_conditions"):
            lines.append(f"- Хронические состояния: {profile['chronic_conditions']}")
    if meds:
        med_strs = [
            " ".join(filter(None, [m["name"], m["dosage"], m["schedule"]]))
            for m in meds[:10]
        ]
        lines.append("- Принимаемые сейчас препараты: " + "; ".join(med_strs))
    if complaints:
        lines.append("- Актуальные жалобы: " + " | ".join(c["text"] for c in complaints))
    return "\n".join(lines) if len(lines) > 1 else None


def _reports_context(conn: sqlite3.Connection, user_id: int) -> str | None:
    """Заключения врачей: диагнозы и рекомендации из последних визитов."""
    rows = conn.execute(_RECENT_REPORTS_SQL, (user_id,)).fetchall()
    if not rows:
        return None
    lines = ["Заключения врачей (последние визиты):"]
    for r in rows:
        parts = [p for p in (r["visit_date"], r["department"], r["doctor_name"]) if p]
        head = ", ".join(str(p) for p in parts)
        if r["diagnosis"]:
            lines.append(f"- [{head}] Диагноз: {r['diagnosis']}")
        try:
            recs = json.loads(r["recommendations_json"] or "[]") or []
        except (json.JSONDecodeError, TypeError):
            recs = []
        if recs:
            lines.append(f"  Рекомендации врача: {'; '.join(str(x) for x in recs[:8])}")
    return "\n".join(lines) if len(lines) > 1 else None


def _external_context(conn: sqlite3.Connection, user_id: int) -> str | None:
    """Погода, геомагнитная активность и (опционально) развлекательный гороскоп.

    Погода запрашивается по координатам из профиля пациента или по умолчанию (Москва).
    Все источники — graceful: при ошибке сети блок просто пропускается.
    """
    lines: list[str] = []

    lat, lon = EXT_DEFAULT_LAT, EXT_DEFAULT_LON
    birth_date = None
    repo = PatientRepo(conn, user_id)
    profile = repo.get_profile()
    if profile:
        if profile.get("latitude"):
            lat = profile["latitude"]
        if profile.get("longitude"):
            lon = profile["longitude"]
        birth_date = profile.get("birth_date")

    if EXT_WEATHER_ENABLED or EXT_GEOMAGNETIC_ENABLED:
        ext = weather.gather_external_context(
            latitude=lat if EXT_WEATHER_ENABLED else None,
            longitude=lon if EXT_WEATHER_ENABLED else None,
        )
        if ext:
            lines.append(ext)

    if EXT_ASTROLOGY_ENABLED:
        horo = astrology.get_daily_horoscope(birth_date)
        if horo:
            lines.append(horo)

    return "\n".join(lines) if lines else None


def build_patient_context(user_id: int) -> str:
    """Профиль/жалобы/препараты + отклонения анализов + назначения + носимые устройства."""
    parts: list[str] = []
    with get_conn() as conn:
        profile_block = _profile_context(conn, user_id)
        if profile_block:
            parts.append(profile_block)

        labs = conn.execute(_RECENT_LABS_SQL, (user_id,)).fetchall()
        if labs:
            facts = build_lab_facts(labs)
            parts.append(render_lab_facts(facts))

        meds_sql = """
            SELECT medications_json, medications_normalized_json, visit_date
            FROM doctor_reports
            WHERE user_id = ? AND medications_json IS NOT NULL
            ORDER BY visit_date DESC LIMIT 3
        """
        meds_rows = conn.execute(meds_sql, (user_id,)).fetchall()
        med_names: list[str] = []
        for r in meds_rows:
            try:
                med_names.extend(json.loads(r["medications_json"]) or [])
            except (json.JSONDecodeError, TypeError):
                continue
        if med_names:
            parts.append("Назначенные врачами лекарства: " + "; ".join(med_names[:15]))

        reports_block = _reports_context(conn, user_id)
        if reports_block:
            parts.append(reports_block)

        health = HealthRepo(conn, user_id)
        since = str(dt.date.today() - dt.timedelta(days=14))
        daily = health.daily_summary(since, str(dt.date.today()) + " 23:59:59")
        if daily:
            by_metric: dict[str, list[dict]] = {}
            for row in daily:
                by_metric.setdefault(row["metric"], []).append(row)
            lines = ["Носимые устройства за 14 дней (дневные агрегаты):"]
            for metric, rows in sorted(by_metric.items()):
                avg = sum(r["avg"] for r in rows) / len(rows)
                unit = rows[0].get("unit") or ""
                lines.append(f"- {metric}: среднее {avg:.1f} {unit}".rstrip())
            parts.append("\n".join(lines))

        ext_block = _external_context(conn, user_id)
        if ext_block:
            parts.append(ext_block)
    return "\n\n".join(parts) if parts else "Данных о пациенте в базе нет."
