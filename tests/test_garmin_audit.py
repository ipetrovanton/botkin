import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "garmin_audit.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("garmin_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["garmin_audit"] = module
    spec.loader.exec_module(module)
    return module


def _package():
    return {
        "patient": {"patient_key": "Петров Антон Игоревич|24.02.1993", "garmin_attached": True},
        "facts": {
            "health": [
                {"id": "HLT:hrv_last_night:2026-01-01", "metric": "hrv_last_night", "date": "2026-01-01", "average": 42.0, "unit": "мс"},
                {"id": "HLT:sleep_seconds:2026-01-01", "metric": "sleep_seconds", "date": "2026-01-01", "average": 28800.0, "unit": "с", "value_json": '{"deepSleepSeconds":7200,"lightSleepSeconds":14400,"remSleepSeconds":5400,"awakeSleepSeconds":1800}'},
            ],
            "activities": [{"id": "ACT:1", "activity_type": "walking", "started_at": "2026-01-01 10:00:00", "duration_s": 1800.0, "distance_m": 2000.0, "avg_hr": 110.0, "max_hr": 130.0, "calories": 120.0}],
            "labs": [], "reports": [], "medications": [], "sources": [],
        },
    }


def test_build_batches_include_sleep_and_activity_ids():
    module = _load_module()

    batches = module.build_batches(_package(), health_batch_size=1)

    assert [batch["expected_ids"] for batch in batches] == [
        ["HLT:hrv_last_night:2026-01-01"], ["HLT:sleep_seconds:2026-01-01"], ["ACT:1"]
    ]


def test_score_garmin_audit_checks_numbers_and_sleep_phases():
    module = _load_module()
    audit = {
        "metrics": [
            {"evidence_ids": ["HLT:hrv_last_night:2026-01-01"], "metric": "hrv_last_night", "date": "2026-01-01", "value_num": 42, "unit": "мс", "sleep_phases": {}},
            {"evidence_ids": ["HLT:sleep_seconds:2026-01-01"], "metric": "sleep_seconds", "date": "2026-01-01", "value_num": 28800, "unit": "с", "sleep_phases": {"deepSleepSeconds": 7200, "lightSleepSeconds": 14400, "remSleepSeconds": 5400, "awakeSleepSeconds": 1800}},
        ],
        "activities": [{"evidence_ids": ["ACT:1"], "activity_type": "walking", "date": "2026-01-01", "duration_s": 1800, "distance_m": 2000, "avg_hr": 110, "max_hr": 130, "calories": 120}],
        "summary": "",
    }

    score = module.score_garmin_audit(module.validate_garmin_audit(audit), _package())

    assert score["passed"] is True
    assert score["metrics"]["matched"] == 2
    assert score["sleep"]["with_phases"] == 1
    assert score["activities"]["matched"] == 1


def test_score_rejects_wrong_metric_value():
    module = _load_module()
    audit = {
        "metrics": [{"evidence_ids": ["HLT:hrv_last_night:2026-01-01"], "metric": "hrv_last_night", "date": "2026-01-01", "value_num": 99, "unit": "мс", "sleep_phases": {}}],
        "activities": [], "summary": "",
    }

    score = module.score_garmin_audit(module.validate_garmin_audit(audit), _package())

    assert score["passed"] is False
    assert score["metrics"]["value_mismatches"] == ["HLT:hrv_last_night:2026-01-01"]
