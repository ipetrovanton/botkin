import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "score_e2e_report.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("score_e2e_report", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["score_e2e_report"] = module
    spec.loader.exec_module(module)
    return module


def test_score_detects_invalid_and_src_citations():
    module = _load_module()
    package = {
        "patient": {
            "patient_key": "Test|01.01.2000",
            "garmin_attached": False,
        },
        "external": {"weather": {"available": False}},
        "facts": {
            "labs": [{"id": "LAB:doc:0"}],
            "lab_series": [],
            "reports": [{"id": "REP:1"}],
            "medications": [],
            "health": [],
            "activities": [],
            "sources": [{"id": "SRC:drug:123"}],
        },
    }
    text = """
# Отчёт

Гемоглобин снижен [LAB:doc:0].
Источник знаний говорит [SRC:drug:123].
Неправильный ID [LAB:doc:999].
"""
    score = module.score_e2e_report(text, package)
    assert score["citation_count"] == 3
    assert score["invalid_ids"] == ["LAB:doc:999"]
    assert score["src_citations"] == ["SRC:drug:123"]
    assert score["passed_guards"] is False


def test_score_passes_when_all_citations_valid():
    module = _load_module()
    package = {
        "patient": {
            "patient_key": "Test|01.01.2000",
            "garmin_attached": False,
        },
        "external": {"weather": {"available": False}},
        "facts": {
            "labs": [{"id": "LAB:doc:0"}],
            "lab_series": [],
            "reports": [{"id": "REP:1"}],
            "medications": [],
            "health": [],
            "activities": [],
            "sources": [],
        },
    }
    text = """
# Отчёт

Факт: гемоглобин 119 г/л [LAB:doc:0].

Интерпретация: возможна анемия [LAB:doc:0].
"""
    score = module.score_e2e_report(text, package)
    assert score["invalid_ids"] == []
    assert score["src_citations"] == []
    assert score["passed_guards"] is True
    assert score["citation_ratio"] > 0


def test_score_detects_garmin_and_weather_leak():
    module = _load_module()
    package = {
        "patient": {
            "patient_key": "Test|01.01.2000",
            "garmin_attached": False,
        },
        "external": {"weather": {"available": False}},
        "facts": {
            "labs": [{"id": "LAB:doc:0"}],
            "lab_series": [],
            "reports": [],
            "medications": [],
            "health": [],
            "activities": [],
            "sources": [],
        },
    }
    text = "Данные Garmin показали hrv 32 мс. Погода влияла на самочувствие."
    score = module.score_e2e_report(text, package)
    assert score["garmin_leak"] is True
    assert score["weather_leak"] is True
    assert score["passed_guards"] is False
