import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "e2e_patient_facts.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("e2e_patient_facts", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["e2e_patient_facts"] = module
    spec.loader.exec_module(module)
    return module


def test_group_documents_does_not_merge_different_patients():
    module = _load_module()
    records = [
        {"filename": "sample_001.pdf", "patient_name": "Антон Петров", "patient_birth_date": "24.02.1993"},
        {"filename": "sample_002.pdf", "patient_name": "Антон Петров", "patient_birth_date": None},
        {"filename": "sample_003.pdf", "patient_name": "Инна Петрова", "patient_birth_date": "24.02.1993"},
    ]

    groups = module.group_documents(records)

    assert set(groups) == {"Антон Петров|24.02.1993", "Инна Петрова|24.02.1993"}
    assert [item["filename"] for item in groups["Антон Петров|24.02.1993"]] == [
        "sample_001.pdf", "sample_002.pdf"
    ]


def test_build_packages_attaches_garmin_only_to_explicit_patient():
    module = _load_module()
    records = [{
        "filename": "sample_001.pdf",
        "patient_name": "Антон Петров",
        "patient_birth_date": "24.02.1993",
        "doc_type": "analysis",
        "analytes": [{"name": "TSH", "value": 6.8, "unit": "мкМЕ/мл", "ref_high": 4.0}],
    }, {
        "filename": "sample_002.pdf",
        "patient_name": "Инна Петрова",
        "patient_birth_date": "24.02.1993",
        "doc_type": "analysis",
        "analytes": [],
    }]
    garmin = [{"id": "HLT:hrv:2026-01-01", "metric": "hrv", "average": 35}, {
        "id": "HLT:sleep_seconds:2026-01-01", "metric": "sleep_seconds", "average": 28800,
        "value_json": '{"deepSleepSeconds": 7200, "lightSleepSeconds": 14400}',
    }]

    packages = module.build_patient_packages(records, garmin_patient_key="Антон Петров|24.02.1993", garmin=garmin)

    assert packages["Антон Петров|24.02.1993"]["patient"]["garmin_attached"] is True
    assert packages["Инна Петрова|24.02.1993"]["patient"]["garmin_attached"] is False
    assert packages["Инна Петрова|24.02.1993"]["facts"]["health"] == []
    assert packages["Инна Петрова|24.02.1993"]["external"]["weather"]["available"] is False
    sleep = next(item for item in packages["Антон Петров|24.02.1993"]["facts"]["health"] if item["metric"] == "sleep_seconds")
    assert "deepSleepSeconds" in sleep["value_json"]


def test_package_marks_missing_analysis_dates_instead_of_inventing():
    module = _load_module()
    records = [{
        "filename": "sample_001.pdf",
        "patient_name": "Антон Петров",
        "patient_birth_date": "24.02.1993",
        "doc_type": "analysis",
        "analytes": [{"name": "TSH", "value": 6.8, "unit": "мкМЕ/мл"}],
    }]

    package = next(iter(module.build_patient_packages(records).values()))

    assert package["facts"]["labs"][0]["taken_at"] is None
    assert package["patient"]["missing_dates"] == ["sample_001.pdf"]
