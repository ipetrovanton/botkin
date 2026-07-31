"""Коннектор Garmin Connect через неофициальную библиотеку garminconnect.

Правила выживания под rate limit Garmin (429 → блок аккаунта на 48 ч):
- Полный логин по паролю — ровно один раз, при подключении. Дальше сессия
  восстанавливается из OAuth-токенов (`login(tokenstore)`), refresh живёт ~30 дней
  и ротируется при каждом использовании.
- Пауза HEALTH_REQUEST_PAUSE между запросами данных.

Пароль пользователя НЕ персистится: он живёт один запрос подключения,
на диск пишутся только токены (data/health_tokens/<user_id>/garmin, вне git).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from garminconnect import Garmin

from botkin.config import HEALTH_REQUEST_PAUSE, HEALTH_SYNC_DAYS, HEALTH_TOKENS_DIR

log = logging.getLogger(__name__)

PROVIDER = "garmin"


def token_dir(user_id: int) -> Path:
    return Path(HEALTH_TOKENS_DIR) / str(user_id) / PROVIDER


def connect(user_id: int, email: str, password: str) -> dict:
    """Первичный логин по паролю; токены сохраняются на диск для последующих синков."""
    path = token_dir(user_id)
    path.mkdir(parents=True, exist_ok=True)
    client = Garmin(email, password)
    client.login(str(path))
    return {"identifier": email, "token_path": str(path), "full_name": client.get_full_name()}


def resume(user_id: int) -> Garmin:
    """Сессия из сохранённых токенов — без пароля и без SSO-логина."""
    path = token_dir(user_id)
    if not path.exists():
        raise FileNotFoundError(f"Нет сохранённых токенов Garmin для пользователя {user_id}")
    client = Garmin()
    client.login(str(path))
    return client


def _pause() -> None:
    time.sleep(HEALTH_REQUEST_PAUSE)


def _day_metrics(client: Garmin, day: str) -> list[dict]:
    """Метрики одного дня. Каждый источник опционален: пустой ответ — не ошибка."""
    rows: list[dict] = []

    hr = client.get_heart_rates(day) or {}
    if hr.get("restingHeartRate") is not None:
        rows.append({"provider": PROVIDER, "metric": "resting_heart_rate",
                     "taken_at": day, "value_num": hr["restingHeartRate"], "unit": "уд/мин"})
    for point in hr.get("heartRateValues") or []:
        ts, value = point[0], point[1]
        if value is None:
            continue
        taken = dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc)
        rows.append({"provider": PROVIDER, "metric": "heart_rate",
                     "taken_at": taken.strftime("%Y-%m-%d %H:%M:%S"),
                     "value_num": value, "unit": "уд/мин"})
    _pause()

    steps = client.get_steps_data(day) or []
    total_steps = sum(s.get("steps") or 0 for s in steps)
    if total_steps:
        rows.append({"provider": PROVIDER, "metric": "steps",
                     "taken_at": day, "value_num": total_steps, "unit": "шагов"})
    _pause()

    sleep = (client.get_sleep_data(day) or {}).get("dailySleepDTO") or {}
    if sleep.get("sleepTimeSeconds"):
        phases = {k: sleep.get(k) for k in
                  ("deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds", "awakeSleepSeconds")}
        rows.append({"provider": PROVIDER, "metric": "sleep_seconds",
                     "taken_at": day, "value_num": sleep["sleepTimeSeconds"], "unit": "с",
                     "value_json": json.dumps(phases, ensure_ascii=False)})
    _pause()

    stress = client.get_all_day_stress(day) or {}
    if (stress.get("avgStressLevel") or 0) > 0:
        rows.append({"provider": PROVIDER, "metric": "stress_avg",
                     "taken_at": day, "value_num": stress["avgStressLevel"], "unit": "балл"})
    bb_values = [
        v[2] for v in (stress.get("bodyBatteryValuesArray") or [])
        if len(v) > 2 and isinstance(v[2], (int, float))
    ]
    if bb_values:
        rows.append({"provider": PROVIDER, "metric": "body_battery_max",
                     "taken_at": day, "value_num": max(bb_values), "unit": "балл"})
    _pause()

    hrv = (client.get_hrv_data(day) or {}).get("hrvSummary") or {}
    if hrv.get("lastNightAvg"):
        rows.append({"provider": PROVIDER, "metric": "hrv_last_night",
                     "taken_at": day, "value_num": hrv["lastNightAvg"], "unit": "мс"})
    _pause()
    return rows


def _blood_pressure(client: Garmin, date_from: str, date_to: str) -> list[dict]:
    """Давление за период одним запросом (Garmin Index BPM или ручные записи)."""
    rows: list[dict] = []
    data = client.get_blood_pressure(date_from, date_to) or {}
    for summary in data.get("measurementSummaries") or []:
        for m in summary.get("measurements") or []:
            taken = m.get("measurementTimestampLocal") or m.get("measurementTimestampGMT")
            if not taken:
                continue
            taken = taken.replace("T", " ")[:19]
            for metric, key in (("blood_pressure_systolic", "systolic"),
                                ("blood_pressure_diastolic", "diastolic"),
                                ("bp_pulse", "pulse")):
                if m.get(key) is not None:
                    rows.append({"provider": PROVIDER, "metric": metric, "taken_at": taken,
                                 "value_num": m[key],
                                 "unit": "мм рт. ст." if "pressure" in metric else "уд/мин"})
    return rows


def _weight(client: Garmin, date_from: str, date_to: str) -> list[dict]:
    rows: list[dict] = []
    data = client.get_weigh_ins(date_from, date_to) or {}
    for daily in data.get("dailyWeightSummaries") or []:
        for m in daily.get("allWeightMetrics") or []:
            if m.get("weight") is None:
                continue
            taken = daily.get("summaryDate") or date_from
            rows.append({"provider": PROVIDER, "metric": "weight_kg", "taken_at": taken,
                         "value_num": round(m["weight"] / 1000, 2), "unit": "кг"})
    return rows


def _activities(client: Garmin, date_from: str, date_to: str) -> list[dict]:
    rows: list[dict] = []
    for a in client.get_activities_by_date(date_from, date_to) or []:
        rows.append({
            "provider": PROVIDER,
            "external_id": str(a.get("activityId")),
            "activity_type": (a.get("activityType") or {}).get("typeKey"),
            "name": a.get("activityName"),
            "started_at": (a.get("startTimeLocal") or "").replace("T", " ")[:19] or None,
            "duration_s": a.get("duration"),
            "distance_m": a.get("distance"),
            "avg_hr": a.get("averageHR"),
            "max_hr": a.get("maxHR"),
            "calories": a.get("calories"),
            "raw_json": json.dumps(a, ensure_ascii=False),
        })
    return rows


def fetch(user_id: int, days: int = HEALTH_SYNC_DAYS,
          on_progress: Callable[[int, int], None] | None = None) -> tuple[list[dict], list[dict]]:
    """Метрики и активности за последние N дней. on_progress(done, total) — коллбек."""
    client = resume(user_id)
    today = dt.date.today()
    date_from, date_to = str(today - dt.timedelta(days=days - 1)), str(today)

    metrics: list[dict] = []
    for i in range(days):
        day = str(today - dt.timedelta(days=i))
        try:
            metrics.extend(_day_metrics(client, day))
        except Exception as e:  # день не должен ронять весь синк
            log.warning("Garmin: день %s пропущен: %s", day, e)
        if on_progress:
            on_progress(i + 1, days + 2)

    metrics.extend(_blood_pressure(client, date_from, date_to))
    metrics.extend(_weight(client, date_from, date_to))
    if on_progress:
        on_progress(days + 1, days + 2)
    activities = _activities(client, date_from, date_to)
    if on_progress:
        on_progress(days + 2, days + 2)
    return metrics, activities
