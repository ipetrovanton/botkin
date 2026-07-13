"""Тесты парсеров Apple Health: export.zip (XML) и JSON Health Auto Export."""
import io
import zipfile

import pytest

from botkin.health.apple import parse_export_zip, parse_hae_payload

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="ru_RU">
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch"
         unit="count/min" startDate="2026-07-01 10:00:00 +0300"
         endDate="2026-07-01 10:00:00 +0300" value="72"/>
 <Record type="HKQuantityTypeIdentifierBloodPressureSystolic" sourceName="Тонометр"
         unit="mmHg" startDate="2026-07-01 09:00:00 +0300"
         endDate="2026-07-01 09:00:00 +0300" value="128"/>
 <Record type="HKQuantityTypeIdentifierBloodPressureDiastolic" sourceName="Тонометр"
         unit="mmHg" startDate="2026-07-01 09:00:00 +0300"
         endDate="2026-07-01 09:00:00 +0300" value="84"/>
 <Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="Apple Watch"
         unit="%" startDate="2026-07-01 03:00:00 +0300"
         endDate="2026-07-01 03:00:00 +0300" value="0.97"/>
 <Record type="HKQuantityTypeIdentifierDietaryWater" sourceName="App"
         unit="mL" startDate="2026-07-01 12:00:00 +0300"
         endDate="2026-07-01 12:00:00 +0300" value="250"/>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch"
         unit="count/min" startDate="2026-07-01 10:05:00 +0300"
         endDate="2026-07-01 10:05:00 +0300" value="not-a-number"/>
</HealthData>"""


def _zip_with(name: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    return buf.getvalue()


def test_parse_export_zip_maps_known_types():
    rows = parse_export_zip(_zip_with("apple_health_export/export.xml", _XML))
    metrics = {r["metric"] for r in rows}
    # Вода — неизвестный тип, битое значение — пропущены.
    assert metrics == {"heart_rate", "blood_pressure_systolic",
                       "blood_pressure_diastolic", "spo2"}
    hr = next(r for r in rows if r["metric"] == "heart_rate")
    assert hr["value_num"] == 72.0
    assert hr["taken_at"] == "2026-07-01 10:00:00"
    assert hr["provider"] == "apple_health"


def test_parse_export_zip_spo2_fraction_to_percent():
    rows = parse_export_zip(_zip_with("export.xml", _XML))
    spo2 = next(r for r in rows if r["metric"] == "spo2")
    assert spo2["value_num"] == 97.0


def test_parse_export_zip_without_xml_raises():
    with pytest.raises(ValueError, match="export.xml"):
        parse_export_zip(_zip_with("readme.txt", "нет данных"))


def test_parse_hae_payload():
    payload = {"data": {"metrics": [
        {"name": "resting_heart_rate", "units": "bpm",
         "data": [{"date": "2026-07-01 00:00:00 +0300", "qty": 58}]},
        {"name": "heart_rate", "units": "bpm",
         "data": [{"date": "2026-07-01 10:00:00 +0300", "Min": 60, "Avg": 72, "Max": 90}]},
        {"name": "sleep_analysis", "units": "hr",
         "data": [{"date": "2026-07-01 08:00:00 +0300", "qty": 7.5}]},
        {"name": "unknown_metric", "data": [{"date": "2026-07-01", "qty": 1}]},
    ]}}
    rows = parse_hae_payload(payload)
    by_metric = {r["metric"]: r for r in rows}
    assert set(by_metric) == {"resting_heart_rate", "heart_rate", "sleep_seconds"}
    assert by_metric["heart_rate"]["value_num"] == 72.0  # без qty берётся Avg
    assert by_metric["sleep_seconds"]["value_num"] == 7.5 * 3600


def test_parse_hae_payload_empty():
    assert parse_hae_payload({}) == []
    assert parse_hae_payload({"data": {"metrics": []}}) == []
