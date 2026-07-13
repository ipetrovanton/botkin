"""Тесты генератора отчёта «ожидания vs реальность» (без Ollama — чистые функции)."""
import importlib.util
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).parent.parent / "scripts" / "bench"


def _load_module():
    """Загружает bench_expectations как модуль (scripts/bench — не пакет)."""
    sys.path.insert(0, str(BENCH_DIR))
    spec = importlib.util.spec_from_file_location(
        "bench_expectations", BENCH_DIR / "bench_expectations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Регистрация ДО exec: иначе dataclass-аннотации не резолвят свой модуль.
    sys.modules["bench_expectations"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_result(mod, *, model, matched, expected, passed, failed, avg_s):
    from bench_models import DocResult, ModelResult  # noqa: PLC0415
    r = ModelResult(model=model)
    r.docs = [DocResult(name=f"doc_{i}.pdf", status="PASS") for i in range(passed)]
    r.docs += [DocResult(name=f"bad_{i}.pdf", status="FAIL", matched=0, expected=2)
               for i in range(failed)]
    r.passed, r.failed = passed, failed
    r.total_matched, r.total_expected = matched, expected
    r.total_s = avg_s * (passed + failed)
    return r


def test_expectation_matching_by_model_prefix():
    """Ожидания находятся по имени модели без тега; незнакомая модель — заглушка «н/д»."""
    mod = _load_module()
    assert "95.22" in mod.expectation_for("glm-ocr:latest").omnidocbench
    assert "32 языках" in mod.expectation_for("qwen3-vl:8b-instruct").russian
    unknown = mod.expectation_for("totally-new-model:1b")
    assert "н/д" in unknown.omnidocbench


def test_report_contains_expectation_and_reality():
    """Отчёт сводит обе стороны: заявленный скор и замеренную точность/скорость."""
    mod = _load_module()
    r = _fake_result(mod, model="glm-ocr", matched=180, expected=200,
                     passed=30, failed=4, avg_s=7.5)
    report = mod.render_report([r], hw="NVIDIA RTX 4090, 24564 MiB")
    assert "glm-ocr" in report
    assert "95.22" in report                       # ожидание
    assert "90.0%" in report                       # реальность: 180/200
    assert "30/34" in report                       # PASS rate
    assert "7.5" in report                         # с/док
    assert "RTX 4090" in report                    # железо прогона
    assert "OmniDocBench не содержит" in report    # оговорка о сравнимости
    assert "bad_0.pdf" in report                   # провалы перечислены


def test_verdict_thresholds():
    """Вердикты: базовая точность проекта (≈100%) — «соответствует», провал — явно."""
    mod = _load_module()
    good = _fake_result(mod, model="qwen3-vl:8b-instruct", matched=200, expected=200,
                        passed=34, failed=0, avg_s=26.0)
    weak = _fake_result(mod, model="glm-ocr", matched=150, expected=200,
                        passed=20, failed=14, avg_s=3.0)
    assert "✅" in mod.verdict(good)
    assert "❌" in mod.verdict(weak)
