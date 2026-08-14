import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "summarize_garmin.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("summarize_garmin", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["summarize_garmin"] = module
    spec.loader.exec_module(module)
    return module


def _audit_module():
    path = MODULE_PATH.parent / "garmin_audit.py"
    spec = importlib.util.spec_from_file_location("garmin_audit_for_summary", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["garmin_audit_for_summary"] = module
    spec.loader.exec_module(module)
    return module


def test_summary_aggregates_verified_metrics_sleep_and_activity():
    module = _load_module()
    audit_module = _audit_module()
    audit = audit_module.validate_garmin_audit({
        "metrics": [
            {"evidence_ids": ["HLT:steps:2026-01-01"], "metric": "steps", "date": "2026-01-01", "value_num": 1000, "unit": "шагов", "sleep_phases": {}},
            {"evidence_ids": ["HLT:steps:2026-01-02"], "metric": "steps", "date": "2026-01-02", "value_num": 3000, "unit": "шагов", "sleep_phases": {}},
            {"evidence_ids": ["HLT:sleep_seconds:2026-01-01"], "metric": "sleep_seconds", "date": "2026-01-01", "value_num": 28800, "unit": "с", "sleep_phases": {"deepSleepSeconds": 7200, "lightSleepSeconds": 14400, "remSleepSeconds": 5400, "awakeSleepSeconds": 1800}},
        ],
        "activities": [{"evidence_ids": ["ACT:1"], "activity_type": "walking", "date": "2026-01-01", "duration_s": 600, "distance_m": 1000, "avg_hr": 100, "max_hr": 120, "calories": 50}],
        "summary": "",
    })
    package = {"patient": {"patient_key": "patient|date"}}
    score = {"passed": True, "provenance": {"cited": 4}, "metrics": {"matched": 3}, "sleep": {"with_phases": 1}, "activities": {"matched": 1}}

    summary = module.build_garmin_summary(audit, package, score)

    steps = next(item for item in summary["metrics"] if item["metric"] == "steps")
    assert steps["average"] == 2000.0
    assert summary["sleep"]["average_hours"] == 8.0
    assert summary["sleep"]["days"][0]["phases_seconds"]["deepSleepSeconds"] == 7200
    assert summary["activities"]["total_distance_m"] == 1000.0
    assert "HLT:sleep_seconds:2026-01-01" in module.render_markdown(summary)


def test_summary_rejects_unverified_audit():
    module = _load_module()
    audit_module = _audit_module()
    audit = audit_module.validate_garmin_audit({"metrics": [], "activities": [], "summary": ""})

    try:
        module.build_garmin_summary(audit, {"patient": {"patient_key": "p"}}, {"passed": False})
    except ValueError as error:
        assert "непроверенного" in str(error)
    else:
        raise AssertionError("unverified audit must be rejected")
