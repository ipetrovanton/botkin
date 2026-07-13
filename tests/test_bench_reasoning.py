"""Проверки парсера reasoning-бенчмарка без запуска Ollama."""
import importlib.util
import sys
from pathlib import Path


BENCH_PATH = Path(__file__).parents[1] / "scripts" / "bench" / "bench_reasoning.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bench_reasoning", BENCH_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_reasoning"] = module
    spec.loader.exec_module(module)
    return module


def test_parse_pytest_output_ignores_summary_line():
    module = _load_module()
    output = """
 tests/test_e2e_reasoning.py::TestReasoningBase::test_model_responds PASSED [ 50%]
 tests/test_e2e_reasoning.py::TestLabAnalysis::test_critical_values_detection FAILED [100%]
 ========================= 1 passed, 1 failed in 12.3s =========================
 """
    tests = module.parse_pytest_output(output)
    assert [(test.name, test.status) for test in tests] == [
        ("TestReasoningBase::test_model_responds", "PASS"),
        ("TestLabAnalysis::test_critical_values_detection", "FAIL"),
    ]
