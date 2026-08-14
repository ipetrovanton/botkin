import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "fact_package.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fact_package", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fact_package"] = module
    spec.loader.exec_module(module)
    return module


def _labs():
    return [
        {
            "id": 12,
            "document_id": 3,
            "taken_at": "2026-02-02",
            "name": "Креатинин",
            "raw_name": "Creatinine",
            "value_num": 1.8,
            "value_text": None,
            "unit": "mg/dL",
            "unit_raw": "mg/dL",
            "ref_low": None,
            "ref_high": None,
            "ref_text": "0.8 - 1.2 mg/dL",
        },
        {
            "id": 11,
            "document_id": 2,
            "taken_at": "2025-01-01",
            "name": "Креатинин",
            "raw_name": "Креатинин",
            "value_num": 1.1,
            "value_text": None,
            "unit": "mg/dL",
            "unit_raw": "мг/дл",
            "ref_low": 0.8,
            "ref_high": 1.2,
            "ref_text": None,
        },
        {
            "id": 13,
            "document_id": 4,
            "taken_at": "2026-02-02",
            "name": "Неизвестный",
            "raw_name": "Неизвестный",
            "value_num": 5.0,
            "value_text": None,
            "unit": None,
            "unit_raw": None,
            "ref_low": None,
            "ref_high": None,
            "ref_text": "отрицательно",
        },
    ]


def test_build_fact_package_is_stable_when_input_order_changes():
    module = _load_module()

    first = module.build_fact_package(labs=_labs(), reports=[], health=[], activities=[], sources=[])
    second = module.build_fact_package(labs=list(reversed(_labs())), reports=[], health=[], activities=[], sources=[])

    assert first["sha256"] == second["sha256"]
    assert first["facts"] == second["facts"]


def test_build_fact_package_preserves_source_ids_and_chronology():
    module = _load_module()

    package = module.build_fact_package(labs=_labs(), reports=[], health=[], activities=[], sources=[])

    creatinine = [fact for fact in package["facts"]["labs"] if fact["name"] == "Креатинин"]
    assert [fact["id"] for fact in creatinine] == ["LAB:2:11", "LAB:3:12"]
    assert [fact["status"] for fact in creatinine] == ["normal", "high"]
    assert package["facts"]["lab_series"][0]["fact_ids"] == ["LAB:2:11", "LAB:3:12"]


def test_build_fact_package_sorts_series_with_missing_and_present_unit():
    module = _load_module()
    labs = _labs()
    labs.append({
        "id": 14,
        "document_id": 5,
        "taken_at": "2026-03-01",
        "name": "Креатинин",
        "raw_name": "Креатинин",
        "value_num": 1.2,
        "value_text": None,
        "unit": None,
        "unit_raw": None,
        "ref_low": None,
        "ref_high": None,
        "ref_text": None,
    })

    package = module.build_fact_package(labs=labs, reports=[], health=[], activities=[], sources=[])

    assert len(package["facts"]["lab_series"]) == 3


def test_build_fact_package_marks_unparseable_reference_unknown():
    module = _load_module()

    package = module.build_fact_package(labs=_labs(), reports=[], health=[], activities=[], sources=[])

    unknown = next(fact for fact in package["facts"]["labs"] if fact["name"] == "Неизвестный")
    assert unknown["status"] == "unknown"


def test_build_fact_package_preserves_medication_provenance():
    module = _load_module()
    reports = [
        {
            "id": 7,
            "visit_date": "2026-02-01",
            "diagnosis": "Тестовый диагноз",
            "doctor_name": "***",
            "department": "Неврология",
            "medications": [
                {"raw": "Триттико 150 мг", "canonical": "Триттико", "schedule": "100 мг вечером"}
            ],
            "recommendations": ["Контроль через месяц"],
        }
    ]

    package = module.build_fact_package(labs=[], reports=reports, health=[], activities=[], sources=[])

    assert package["facts"]["reports"][0]["id"] == "REP:7"
    assert package["facts"]["medications"] == [
        {
            "canonical": "Триттико",
            "id": "MED:7:0",
            "raw": "Триттико 150 мг",
            "report_id": "REP:7",
            "schedule": "100 мг вечером",
        }
    ]
