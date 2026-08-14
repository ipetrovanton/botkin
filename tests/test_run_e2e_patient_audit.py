import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "run_e2e_patient_audit.py"


def _load_module():
    sys.path.insert(0, str(MODULE_PATH.parent))
    spec = importlib.util.spec_from_file_location("run_e2e_patient_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_e2e_patient_audit"] = module
    spec.loader.exec_module(module)
    return module


def test_safe_key_replaces_non_word_chars():
    module = _load_module()
    assert module._safe_key("Петров Антон|24.02.1993") == "Петров_Антон_24.02.1993"


def test_load_patient_packages_skips_manifest(tmp_path):
    module = _load_module()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps([{"patient_key": "ignored"}]), encoding="utf-8")
    package_path = tmp_path / "patient.json"
    package_path.write_text(json.dumps({"patient": {"patient_key": "Patient A"}}), encoding="utf-8")

    loaded = module.load_patient_packages(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["patient"]["patient_key"] == "Patient A"
