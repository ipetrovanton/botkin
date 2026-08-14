import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "deep_model_benchmark.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("deep_model_benchmark", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["deep_model_benchmark"] = module
    spec.loader.exec_module(module)
    return module


def test_merge_stream_message_prefers_accumulated_chunks_over_empty_done_fields():
    module = _load_module()
    result = {"message": {"content": "", "thinking": ""}}

    merged = module.merge_stream_message(result, ["часть 1", " часть 2"], ["мысль"])

    assert merged["message"] == {"content": "часть 1 часть 2", "thinking": "мысль"}


def test_extract_evidence_ids_deduplicates_and_sorts():
    module = _load_module()

    assert module.extract_evidence_ids("[LAB:3:12] [REP:7] [LAB:3:12]") == {
        "LAB:3:12",
        "REP:7",
    }


def test_score_evidence_ids_separates_unknown_citations():
    module = _load_module()

    score = module.score_evidence_ids(
        {"LAB:3:12", "REP:7", "UNKNOWN:1"}, {"LAB:3:12", "REP:7", "MED:7:0"}
    )

    assert score == {
        "cited": 3,
        "valid": 2,
        "invalid": ["UNKNOWN:1"],
        "precision": 2 / 3,
    }


def test_claim_set_jaccard_is_one_for_identical_sets():
    module = _load_module()

    assert module.claim_set_jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_claim_set_jaccard_handles_empty_sets():
    module = _load_module()

    assert module.claim_set_jaccard(set(), set()) == 1.0
    assert module.claim_set_jaccard({"a"}, set()) == 0.0


def test_load_fact_package_uses_all_fact_domains(tmp_path):
    module = _load_module()
    db_path = tmp_path / "facts.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE lab_results (id INTEGER, document_id INTEGER, user_id INTEGER, analyte_canonical TEXT, analyte_name TEXT, value_num REAL, value_text TEXT, unit TEXT, unit_raw TEXT, ref_low REAL, ref_high REAL, ref_text TEXT, taken_at TEXT);
            CREATE TABLE doctor_reports (id INTEGER, user_id INTEGER, visit_date TEXT, diagnosis TEXT, doctor_name TEXT, department TEXT, medications_json TEXT, recommendations_json TEXT);
            CREATE TABLE health_metrics (id INTEGER, user_id INTEGER, provider TEXT, metric TEXT, taken_at TEXT, value_num REAL, unit TEXT);
            CREATE TABLE health_activities (id INTEGER, user_id INTEGER, provider TEXT, external_id TEXT, activity_type TEXT, started_at TEXT, duration_s REAL, distance_m REAL, calories REAL, avg_hr REAL, max_hr REAL);
            CREATE TABLE rag_chunks (id INTEGER, source TEXT, ref_key TEXT, text TEXT, meta_json TEXT);
            """
        )
        conn.execute("INSERT INTO lab_results VALUES (1, 4, 9, 'TSH', 'ТТГ', 6.8, NULL, 'мкМЕ/мл', 'мкМЕ/мл', 0.4, 4, NULL, '2026-01-01')")
        conn.execute("INSERT INTO doctor_reports VALUES (2, 9, '2026-01-02', 'Диагноз', '***', 'Эндокринология', '[\"Левотироксин 50 мкг\"]', '[\"Контроль TSH\"]')")
        conn.execute("INSERT INTO health_metrics VALUES (3, 9, 'garmin', 'hrv', '2026-01-03', 35, 'мс')")
        conn.execute("INSERT INTO health_activities VALUES (4, 9, 'garmin', 'a1', 'running', '2026-01-04', 1800, 5000, 320, 140, 170)")
        conn.execute("INSERT INTO rag_chunks VALUES (5, 'research', 'PMID:1', 'Исследование', '{\"pmid\": \"1\"}')")

    package = module.load_fact_package(db_path, 9)

    assert package["facts"]["labs"][0]["id"] == "LAB:4:1"
    assert package["facts"]["reports"][0]["id"] == "REP:2"
    assert package["facts"]["health"][0]["id"] == "HLT:hrv:2026-01-03"
    assert package["facts"]["activities"][0]["id"] == "ACT:4"
    assert package["facts"]["sources"][0]["id"] == "SRC:5"


def test_atomic_write_json_replaces_complete_file(tmp_path):
    module = _load_module()
    path = tmp_path / "result.json"
    path.write_text('{"previous": true}', encoding="utf-8")

    module.atomic_write_json(path, {"status": "done", "samples": 3})

    assert json.loads(path.read_text(encoding="utf-8")) == {"samples": 3, "status": "done"}
    assert not path.with_suffix(".json.tmp").exists()


def test_all_fact_ids_collects_every_domain():
    module = _load_module()
    package = {
        "facts": {
            "labs": [{"id": "LAB:1:1"}],
            "lab_series": [{"fact_ids": ["LAB:1:1"]}],
            "reports": [{"id": "REP:2"}],
            "medications": [{"id": "MED:2:0"}],
            "health": [{"id": "HLT:x"}],
            "activities": [{"id": "ACT:3"}],
            "sources": [{"id": "SRC:4"}],
        }
    }

    assert module.all_fact_ids(package) == {
        "LAB:1:1", "REP:2", "MED:2:0", "HLT:x", "ACT:3", "SRC:4"
    }


def _audit_fixture(module):
    package = {
        "facts": {
            "labs": [{
                "id": "LAB:4:1", "name": "TSH", "value_num": 6.8,
                "unit": "мкМЕ/мл", "status": "high", "taken_at": "2026-01-01",
            }, {
                "id": "LAB:4:2", "name": "T4", "value_num": 12.0,
                "unit": "пмоль/л", "status": "normal", "taken_at": "2026-01-01",
            }],
            "lab_series": [],
            "reports": [{"id": "REP:2", "visit_date": "2026-01-02", "diagnosis": "Тест"}],
            "medications": [{
                "id": "MED:2:0", "raw": "Левотироксин 50 мкг",
                "canonical": "Левотироксин", "schedule": "утром", "report_id": "REP:2",
            }],
            "health": [],
            "activities": [],
            "sources": [{"id": "SRC:5", "source": "research", "text": "Источник"}],
        }
    }
    audit = {
        "lab_assertions": [{
            "evidence_ids": ["LAB:4:1"], "name": "TSH", "value_num": 6.8,
            "unit": "мкМЕ/мл", "status": "high",
        }],
        "date_assertions": [{"evidence_ids": ["REP:2"], "date": "2026-01-02"}],
        "medication_assertions": [{
            "evidence_ids": ["MED:2:0"], "raw": "Левотироксин 50 мкг",
            "canonical": "Левотироксин", "schedule": "утром",
        }],
        "contradictions": [{"evidence_ids": ["LAB:4:1", "LAB:4:2"], "description": "Проверить"}],
        "findings": [{"type": "FACT", "text": "TSH выше референса", "evidence_ids": ["LAB:4:1"], "confidence": "high"}],
        "missing_data": [],
    }
    golden = {
        "required_lab_ids": ["LAB:4:1"],
        "required_date_ids": ["REP:2"],
        "required_medication_ids": ["MED:2:0"],
        "required_contradiction_groups": [["LAB:4:1", "LAB:4:2"]],
        "required_finding_evidence": 1,
    }
    return package, audit, golden


def test_structured_audit_schema_accepts_complete_fixture():
    module = _load_module()
    package, audit, golden = _audit_fixture(module)

    parsed = module.validate_fact_audit(audit)
    score = module.score_fact_audit(parsed, package, golden)

    assert score["passed"] is True
    assert score["labs"]["matched"] == 1
    assert score["dates"]["matched"] == 1
    assert score["medications"]["matched"] == 1
    assert score["contradictions"]["matched"] == 1
    assert score["findings"]["with_valid_evidence"] == 1


def test_golden_scorer_rejects_wrong_lab_value():
    module = _load_module()
    package, audit, golden = _audit_fixture(module)
    audit["lab_assertions"][0]["value_num"] = 5.8

    score = module.score_fact_audit(module.validate_fact_audit(audit), package, golden)

    assert score["passed"] is False
    assert score["labs"]["value_mismatches"] == ["LAB:4:1"]


def test_golden_scorer_rejects_unknown_evidence_id():
    module = _load_module()
    package, audit, golden = _audit_fixture(module)
    audit["findings"][0]["evidence_ids"] = ["LAB:missing"]

    score = module.score_fact_audit(module.validate_fact_audit(audit), package, golden)

    assert score["passed"] is False
    assert score["provenance"]["invalid_ids"] == ["LAB:missing"]


def test_build_prompts_require_evidence_ids_and_fact_types():
    module = _load_module()
    package = {"schema_version": 1, "sha256": "abc123", "facts": {"labs": []}}

    audit_system, audit_user = module.build_audit_prompts(package)
    synthesis_system, synthesis_user = module.build_synthesis_prompts(package)

    for text in (audit_system, audit_user, synthesis_system, synthesis_user):
        assert "evidence_ids" in text
    assert "ФАКТ" in synthesis_system
    assert "ИНТЕРПРЕТАЦИЯ" in synthesis_system
    assert "ГИПОТЕЗА" in synthesis_system
    assert "abc123" in audit_user
