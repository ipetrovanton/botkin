"""Приём данных Apple Health двумя путями (облачного API у Apple нет):

1. `export.zip` из приложения «Здоровье» (Профиль → Экспорт медданных):
   внутри export.xml, у долгих пользователей >1 ГБ — парсим потоково через
   iterparse, в память загружается одна запись за раз.
2. JSON от приложения Health Auto Export (автоматизация REST API — телефон сам
   шлёт метрики на наш эндпоинт).
"""
from __future__ import annotations

import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from typing import IO

log = logging.getLogger(__name__)

PROVIDER = "apple_health"

# HK-тип → (наша метрика, единица). Остальные типы пропускаем: тащить все 100+
# категорий в time-series нет смысла, ядро анализа — сердце/давление/сон/активность.
_HK_TYPES: dict[str, tuple[str, str]] = {
    "HKQuantityTypeIdentifierHeartRate": ("heart_rate", "уд/мин"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_heart_rate", "уд/мин"),
    "HKQuantityTypeIdentifierBloodPressureSystolic": ("blood_pressure_systolic", "мм рт. ст."),
    "HKQuantityTypeIdentifierBloodPressureDiastolic": ("blood_pressure_diastolic", "мм рт. ст."),
    "HKQuantityTypeIdentifierStepCount": ("steps_interval", "шагов"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv_sdnn", "мс"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("spo2", "%"),
    "HKQuantityTypeIdentifierBodyMass": ("weight_kg", "кг"),
}

# Метрики Health Auto Export (ключ metric.name) → наши.
_HAE_METRICS: dict[str, tuple[str, str]] = {
    "heart_rate": ("heart_rate", "уд/мин"),
    "resting_heart_rate": ("resting_heart_rate", "уд/мин"),
    "blood_pressure_systolic": ("blood_pressure_systolic", "мм рт. ст."),
    "blood_pressure_diastolic": ("blood_pressure_diastolic", "мм рт. ст."),
    "step_count": ("steps", "шагов"),
    "heart_rate_variability": ("hrv_sdnn", "мс"),
    "blood_oxygen_saturation": ("spo2", "%"),
    "weight_body_mass": ("weight_kg", "кг"),
    "sleep_analysis": ("sleep_seconds", "с"),
}


def parse_export_zip(payload: bytes, max_records: int = 2_000_000) -> list[dict]:
    """export.zip → строки health_metrics. Потоково, без загрузки XML целиком."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        xml_name = next(
            (n for n in zf.namelist() if n.endswith("export.xml")), None
        )
        if xml_name is None:
            raise ValueError("В архиве нет export.xml — это не экспорт Apple Health")
        with zf.open(xml_name) as fh:
            return _parse_export_xml(fh, max_records)


def _parse_export_xml(fh: IO[bytes], max_records: int) -> list[dict]:
    rows: list[dict] = []
    for _, elem in ET.iterparse(fh, events=("end",)):
        if elem.tag != "Record":
            continue
        mapping = _HK_TYPES.get(elem.get("type") or "")
        if mapping:
            metric, unit = mapping
            taken = (elem.get("startDate") or "")[:19]
            try:
                value = float(elem.get("value"))
            except (TypeError, ValueError):
                elem.clear()
                continue
            if metric == "spo2" and value <= 1.0:
                value = round(value * 100, 1)
            rows.append({"provider": PROVIDER, "metric": metric, "taken_at": taken,
                         "value_num": value, "unit": unit})
            if len(rows) >= max_records:
                log.warning("export.xml: достигнут потолок %d записей, остальное пропущено",
                            max_records)
                break
        elem.clear()  # освобождаем память — файл может быть гигабайтным
    return rows


def parse_hae_payload(payload: dict) -> list[dict]:
    """JSON от Health Auto Export (Automations → REST API, формат JSON).

    Структура: {"data": {"metrics": [{"name", "units", "data": [{"date", "qty"...}]}]}}.
    """
    rows: list[dict] = []
    metrics = ((payload.get("data") or {}).get("metrics")) or []
    for metric_block in metrics:
        mapping = _HAE_METRICS.get((metric_block.get("name") or "").lower())
        if not mapping:
            continue
        metric, unit = mapping
        for point in metric_block.get("data") or []:
            taken = (point.get("date") or "")[:19]
            value = point.get("qty")
            if value is None:  # heart_rate шлёт Min/Avg/Max вместо qty
                value = point.get("Avg") or point.get("avg")
            if metric == "sleep_seconds" and value is not None:
                value = float(value) * 3600  # HAE отдаёт сон в часах
            if not taken or value is None:
                continue
            extra = {k: v for k, v in point.items() if k not in ("date", "qty")}
            rows.append({
                "provider": PROVIDER, "metric": metric, "taken_at": taken,
                "value_num": float(value), "unit": unit,
                "value_json": json.dumps(extra, ensure_ascii=False) if extra else None,
            })
    return rows
