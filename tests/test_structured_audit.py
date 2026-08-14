import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "structured_audit.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("structured_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["structured_audit"] = module
    spec.loader.exec_module(module)
    return module


def _package():
    return {
        "facts": {
            "labs": [{"id": f"LAB:{i}", "name": "TSH"} for i in range(5)],
            "lab_series": [{"fact_ids": ["LAB:0", "LAB:1"]}],
            "reports": [{"id": "REP:1", "visit_date": "2026-01-01"}],
            "medications": [{"id": f"MED:{i}"} for i in range(3)],
            "health": [],
            "activities": [],
            "sources": [],
        }
    }


def test_domain_batches_preserve_ids_and_split_labs():
    module = _load_module()

    batches = module.domain_batches(_package(), lab_batch_size=2, medication_batch_size=2)

    lab_batches = [batch for batch in batches if batch["domain"] == "labs"]
    assert [batch["expected_ids"] for batch in lab_batches] == [
        ["LAB:0", "LAB:1"], ["LAB:2", "LAB:3"], ["LAB:4"]
    ]
    assert {item["id"] for batch in batches for item in batch["facts"]} >= {
        "LAB:0", "LAB:1", "LAB:2", "LAB:3", "LAB:4", "REP:1", "MED:0", "MED:1", "MED:2"
    }


def test_merge_audits_concatenates_domains_without_losing_assertions():
    module = _load_module()

    merged = module.merge_audits([
        {"lab_assertions": [{"evidence_ids": ["LAB:0"]}], "findings": []},
        {"lab_assertions": [{"evidence_ids": ["LAB:1"]}], "findings": [{"evidence_ids": ["REP:1"]}]},
    ])

    assert [item["evidence_ids"] for item in merged["lab_assertions"]] == [["LAB:0"], ["LAB:1"]]
    assert merged["findings"] == [{"evidence_ids": ["REP:1"]}]


def test_audit_config_is_deterministic_and_disables_thinking():
    module = _load_module()

    config = module.audit_config()

    assert config["temperature"] == 0.0
    assert config["seed"] == 42
    assert config["think"] is False
    assert config["num_ctx"] == 16384
    assert config["num_predict"] == 8192


def test_findings_batch_uses_abnormal_labs_reports_and_medications():
    module = _load_module()

    package = {
        "patient": {"patient_key": "Test Patient|01.01.2000"},
        "facts": {
            "labs": [
                {"id": "LAB:1", "name": "HGB", "status": "low"},
                {"id": "LAB:2", "name": "HGB", "status": "normal"},
                {"id": "LAB:3", "name": "TSH", "status": "high"},
            ],
            "lab_series": [],
            "reports": [{"id": "REP:1", "visit_date": "2026-01-01"}],
            "medications": [{"id": "MED:1", "raw": "foo"}],
            "health": [],
            "activities": [],
            "sources": [],
        },
    }

    batches = module.domain_batches(package, include_findings=True)
    findings_batches = [batch for batch in batches if batch["domain"] == "findings"]
    assert len(findings_batches) == 1
    assert set(findings_batches[0]["expected_ids"]) == {"LAB:1", "LAB:3", "REP:1", "MED:1"}


def test_build_golden_reflects_findings_switch():
    module = _load_module()

    package = {
        "facts": {
            "labs": [{"id": "LAB:1", "name": "HGB", "status": "low"}],
            "lab_series": [],
            "reports": [],
            "medications": [],
            "health": [],
            "activities": [],
            "sources": [],
        }
    }

    golden_without = module.build_golden(package, include_findings=False)
    golden_with = module.build_golden(package, include_findings=True)
    assert golden_without["required_finding_evidence"] == 0
    assert golden_with["required_finding_evidence"] == 1
