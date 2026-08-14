import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "summarize_patient.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("summarize_patient", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["summarize_patient"] = module
    spec.loader.exec_module(module)
    return module


def test_build_patient_summary_aggregates_labs_and_preserves_evidence():
    module = _load_module()
    package = {
        "patient": {
            "patient_key": "Test Patient|01.01.2000",
            "birth_date": "01.01.2000",
            "garmin_attached": False,
            "missing_dates": [],
        },
        "external": {"weather": {"available": False}},
        "facts": {
            "labs": [
                {
                    "id": "LAB:doc:0",
                    "document_id": "doc",
                    "result_id": 0,
                    "taken_at": "2026-01-10",
                    "name": "HGB",
                    "raw_name": "HGB",
                    "value_num": 119.0,
                    "value_text": None,
                    "unit": "g/l",
                    "unit_raw": "g/l",
                    "reference": "132-173",
                    "ref_low": 132.0,
                    "ref_high": 173.0,
                    "status": "low",
                },
                {
                    "id": "LAB:doc:1",
                    "document_id": "doc",
                    "result_id": 1,
                    "taken_at": "2026-02-10",
                    "name": "HGB",
                    "raw_name": "HGB",
                    "value_num": 145.0,
                    "value_text": None,
                    "unit": "g/l",
                    "unit_raw": "g/l",
                    "reference": "132-173",
                    "ref_low": 132.0,
                    "ref_high": 173.0,
                    "status": "normal",
                },
            ],
            "lab_series": [{"name": "HGB", "unit": "g/l", "fact_ids": ["LAB:doc:0", "LAB:doc:1"], "observations": 2}],
            "reports": [
                {
                    "id": "REP:report",
                    "report_id": "report",
                    "visit_date": "2026-01-15",
                    "diagnosis": "Анемия",
                    "doctor_name": "Иванов",
                    "department": "Терапия",
                    "recommendations": ["железо"],
                }
            ],
            "medications": [
                {"id": "MED:report:0", "report_id": "REP:report", "raw": "феррум", "canonical": "Феррум", "schedule": "1 таб"}
            ],
            "health": [],
            "activities": [],
            "sources": [],
        },
    }
    audit = module.validate_fact_audit({
        "lab_assertions": [
            {"evidence_ids": ["LAB:doc:0"], "name": "HGB", "value_num": 119.0, "unit": "g/l", "status": "low"},
            {"evidence_ids": ["LAB:doc:1"], "name": "HGB", "value_num": 145.0, "unit": "g/l", "status": "normal"},
        ],
        "date_assertions": [
            {"evidence_ids": ["REP:report"], "date": "2026-01-15"},
        ],
        "medication_assertions": [
            {"evidence_ids": ["MED:report:0"], "raw": "феррум", "canonical": "Феррум", "schedule": "1 таб"},
        ],
        "contradictions": [
            {"evidence_ids": ["LAB:doc:0", "LAB:doc:1"], "description": "HGB нормализовался"},
        ],
        "findings": [
            {"type": "FACT", "text": "HGB снижался", "confidence": "high", "evidence_ids": ["LAB:doc:0"]},
        ],
        "missing_data": [],
    })
    score = {
        "passed": True,
        "provenance": {"invalid_ids": [], "total_ids": 5},
    }
    summary = module.build_patient_summary(audit, package, score)
    assert summary["patient_key"] == "Test Patient|01.01.2000"
    assert len(summary["labs"]) == 1
    hgb_series = summary["labs"][0]
    assert hgb_series["name"] == "HGB"
    assert hgb_series["count"] == 2
    assert hgb_series["minimum"] == 119.0
    assert hgb_series["maximum"] == 145.0
    assert hgb_series["values"][0]["evidence_id"] == "LAB:doc:0"
    assert summary["reports"][0]["doctor_name"] == "Иванов"
    assert summary["medications"][0]["raw"] == "феррум"
    assert summary["findings"][0]["type"] == "FACT"
    markdown = module.render_markdown(summary)
    assert "HGB" in markdown
    assert "LAB:doc:0" in markdown
    assert "REP:report" in markdown
    assert "MED:report:0" in markdown


def test_summary_raises_if_audit_not_passed():
    module = _load_module()
    package = {"patient": {"patient_key": "X"}, "external": {"weather": {"available": False}}, "facts": {}}
    audit = module.validate_fact_audit({
        "lab_assertions": [],
        "date_assertions": [],
        "medication_assertions": [],
        "contradictions": [],
        "findings": [],
        "missing_data": [],
    })
    score = {"passed": False}
    try:
        module.build_patient_summary(audit, package, score)
        assert False, "ожидалось исключение"
    except ValueError as exc:
        assert "непроверенного" in str(exc)
