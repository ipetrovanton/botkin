"""E2E-проверки распознавания через РЕАЛЬНУЮ Ollama (без подмены LLM).

Назначение — автоматизировать smoke-проверку «всё работает целиком»: модель поднята,
классификация и извлечение реально вызываются и дают осмысленный результат. Поэтому здесь
ничего не мокается — гоняем боевые classify.run_vlm и extract.run_analysis.

Запуск только этих тестов:
    uv run pytest -m llm -s

Без запущенной Ollama (или без нужной модели) тесты авто-skip — чтобы обычный прогон в CI
без GPU оставался зелёным. Скорость каждого вызова печатается ([SPEED]) и проверяется на
грубый потолок: ловим зависшую/деградировавшую модель, не флуктуации железа.

Источники документов:
  1. Синтетический бланк make_lab_pdf — строгий ground-truth, в репозитории, повторяемо.
  2. Реальные PDF/фото из каталога BOTKIN_E2E_DOCS_DIR (по умолчанию tests/fixtures/documents)
     — боевая проверка VLM на настоящих сканах. Если каталог пуст — соответствующий тест skip.
     Рядом с документом можно положить sidecar <имя>.expected.json с разметкой — тогда
     сверка строгая (см. tests/fixtures/documents/README.md). Без sidecar — мягкая проверка.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import e2e_report as er
from botkin.config import VLM_MODEL
from botkin.llm import classify
from botkin.llm import extract as ex
from botkin.llm.client import _detect_ollama_url, _is_url_reachable, get_backend

# Отчёт печатает единицы с надстрочными символами (10⁹/л, 10¹²/л). При запуске через
# WSL python.exe с выводом в cp1251-консоль PowerShell такой символ роняет print
# (UnicodeEncodeError). errors="replace" заменяет некодируемое на '?', не теряя поток.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):  # поток без reconfigure (перехвачен/перенаправлен)
    pass

# Потолки времени щедрые: одиночный VLM-запрос ограничен VLM_REQUEST_TIMEOUT (120 с),
# extract может сделать несколько вызовов. Цель ассерта — поймать зависание, не мерить железо.
# На медленных VLM-моделях бюджеты можно увеличить через env.
_CLASSIFY_BUDGET_S = float(os.environ.get("E2E_CLASSIFY_BUDGET_S", "180.0"))
_EXTRACT_BUDGET_S = float(os.environ.get("E2E_EXTRACT_BUDGET_S", "900.0"))

_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

# Все отчёты по документам — для сводки и сохранения в benchmarks/.
reports: list[er.DocReport] = []


def _backend_skip_reason() -> str | None:
    """None если текущий backend доступен; иначе причина для skip."""
    backend = get_backend()
    if backend == "ollama":
        url = _detect_ollama_url()
        if not _is_url_reachable(url):
            return f"Ollama недоступна по {url} — пропускаю e2e"
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as resp:
                tags = json.load(resp)
        except (OSError, ValueError) as e:
            return f"не удалось прочитать список моделей Ollama: {e}"
        names = {m.get("name", "") for m in tags.get("models", [])}
        base = VLM_MODEL.split(":", 1)[0]
        has_model = any(n == VLM_MODEL or n.split(":", 1)[0] == base for n in names)
        if not has_model:
            return f"модель {VLM_MODEL} не загружена в Ollama"
        return None
    # vllm / mlx: проверяем /v1/models
    if backend == "vllm":
        url = os.getenv("VLLM_URL", "http://localhost:8001")
    else:
        url = os.getenv("MLX_URL", "http://localhost:8002")
    try:
        with urllib.request.urlopen(f"{url}/v1/models", timeout=10) as resp:
            data = json.load(resp)
        model_ids = [m.get("id", "") for m in data.get("data", [])]
        if not model_ids:
            return f"{backend}: список моделей пуст"
        print(f"[{backend}] доступные модели: {model_ids}")
    except (OSError, ValueError) as e:
        return f"{backend} server недоступен по {url}: {e} — пропускаю e2e"
    return None


def _docs_dir() -> Path:
    raw = os.getenv("BOTKIN_E2E_DOCS_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).parent / "fixtures" / "documents"


def _discover_documents() -> list[Path]:
    base = _docs_dir()
    root = base / "samples" if (base / "samples").is_dir() else base
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_file() and p.suffix.lower() in _DOC_EXTENSIONS)


# Известная полная панель ОАК (ground-truth). (name, value, unit, ref).
_CBC_GROUND_TRUTH = [
    ("Гемоглобин", "137", "г/л", "117 - 155"),
    ("Эритроциты", "4.64", "10^12/л", "3.8 - 5.1"),
    ("Гематокрит", "40.8", "%", "35 - 45"),
    ("Тромбоциты", "217", "10^9/л", "150 - 400"),
    ("Лейкоциты", "5.15", "10^9/л", "4.5 - 11"),
    ("Нейтрофилы", "44.6", "%", "47 - 72"),
    ("Лимфоциты", "43.1", "%", "19 - 37"),
    ("Моноциты", "9.7", "%", "3 - 11"),
    ("Базофилы", "0.6", "%", "< 1.0"),
]


def _load_expected(doc_path: Path) -> dict | None:
    """Sidecar-разметка <имя>.expected.json рядом с документом или None, если её нет."""
    sidecar = doc_path.with_suffix(".expected.json")
    if not sidecar.is_file():
        return None
    with sidecar.open(encoding="utf-8") as f:
        return json.load(f)


def _expected_analytes_from_ground_truth() -> list[dict]:
    """Превращает _CBC_GROUND_TRUTH в формат, понятный er.compare_analytes."""
    return [
        {"name": name, "value": _to_number(value), "unit": unit}
        for name, value, unit, _ in _CBC_GROUND_TRUTH
    ]


def _to_number(value: str) -> float | None:
    """Безопасный parse float; '< 1.0' и т.п. не сверяем по числу."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


pytestmark = pytest.mark.llm


@pytest.fixture(scope="module", autouse=True)
def _require_backend():
    reason = _backend_skip_reason()
    if reason:
        pytest.skip(reason, allow_module_level=True)


@pytest.fixture(scope="module", autouse=True)
def _print_summary():
    """После всех документов печатает сводную таблицу и сохраняет JSON-отчёт."""
    yield
    if not reports:
        return

    er.print_summary(reports)

    benchmark_path = Path("benchmarks") / f"e2e_llm_{time.strftime('%Y%m%d_%H%M%S')}.json"
    er.save_benchmark(reports, benchmark_path)


def test_e2e_synthetic_cbc_classified_and_extracted(make_lab_pdf, tmp_path):
    """Синтетический бланк ОАК: реальный classify+extract, структурный diff + метрики."""
    pdf = tmp_path / "e2e_cbc.pdf"
    make_lab_pdf(pdf, _CBC_GROUND_TRUTH, lab="ИНВИТРО",
                 title="Общий анализ крови", rows_per_page=20)

    report = er.DocReport(name=pdf.name, doc_type_expected="analysis")

    with er.MetricsCapture() as capture:
        t0 = time.perf_counter()
        classified = classify.run_vlm(pdf)
        report.classify_s = time.perf_counter() - t0
        report.classify_metrics = list(capture.records)

    print(f"\n[SPEED] classify (синтетика): {report.classify_s:.1f}s -> "
          f"{classified.doc_type} (conf={classified.confidence:.2f})")
    assert report.classify_s < _CLASSIFY_BUDGET_S, f"classify завис: {report.classify_s:.1f}s"
    report.doc_type_got = classified.doc_type
    assert report.doc_type_got == "analysis", f"бланк ОАК классифицирован как {report.doc_type_got}"

    with er.MetricsCapture() as capture:
        t0 = time.perf_counter()
        rows = ex.run_analysis(pdf)
        report.extract_s = time.perf_counter() - t0
        report.extract_metrics = list(capture.records)

    print(f"[SPEED] extract (синтетика): {report.extract_s:.1f}s -> {len(rows)} строк")
    assert report.extract_s < _EXTRACT_BUDGET_S, f"extract завис: {report.extract_s:.1f}s"

    report.diff = er.compare_analytes(_expected_analytes_from_ground_truth(), rows)
    print(f"[E2E] распознано {report.matched_values}/{report.expected_values} "
          f"(precision={report.diff.precision:.2f}, recall={report.diff.recall:.2f})")

    if report.diff.recall < 0.7:
        report.fail_reasons.append(
            f"распознано лишь {report.matched_values}/{report.expected_values}: "
            f"{[r.analyte_name for r in rows]}"
        )
    if report.fail_reasons:
        report.status = "FAIL"

    reports.append(report)
    er.print_report(report)
    assert not report.fail_reasons, "; ".join(report.fail_reasons)


@pytest.mark.parametrize("doc_path", _discover_documents() or [None],
                         ids=lambda p: p.name if p else "no-documents")
def test_e2e_real_document_pipeline(doc_path):
    """Реальный документ: classify+extract с метриками и структурным diff."""
    if doc_path is None:
        pytest.skip(
            f"нет реальных документов в {_docs_dir()}; задайте BOTKIN_E2E_DOCS_DIR "
            "или положите PDF/фото в tests/fixtures/documents")

    expected = _load_expected(doc_path) or {}
    report = er.DocReport(name=doc_path.name, doc_type_expected=expected.get("doc_type"))
    reports.append(report)
    rows = []

    with er.MetricsCapture() as capture:
        t0 = time.perf_counter()
        classified = classify.run_vlm(doc_path)
        report.classify_s = time.perf_counter() - t0
        report.classify_metrics = list(capture.records)

    report.doc_type_got = classified.doc_type

    doctor_reports: list = []
    if classified.doc_type == "analysis":
        with er.MetricsCapture() as capture:
            t0 = time.perf_counter()
            rows = ex.run_analysis(doc_path)
            report.extract_s = time.perf_counter() - t0
            report.extract_metrics = list(capture.records)
    elif classified.doc_type == "doctor_report":
        # Раньше e2e для заключений проверял только doc_type — extract не вызывался.
        with er.MetricsCapture() as capture:
            t0 = time.perf_counter()
            doctor_reports = ex.run_doctor_report(doc_path)
            report.extract_s = time.perf_counter() - t0
            report.extract_metrics = list(capture.records)

    if report.doc_type_expected and report.doc_type_got != report.doc_type_expected:
        report.fail_reasons.append(
            f"doc_type: получено '{report.doc_type_got}', ожидался '{report.doc_type_expected}'")

    analytes = expected.get("analytes") or []
    if classified.doc_type == "analysis" and analytes:
        report.diff = er.compare_analytes(analytes, rows)
        if report.missing:
            report.fail_reasons.append(
                f"не найдено {len(report.missing)}/{report.expected_values} эталонных значений")

    if (
        classified.doc_type == "doctor_report"
        and er.has_doctor_report_content(expected)
    ):
        report.report_diff = er.compare_doctor_reports(expected, doctor_reports)
        rd = report.report_diff
        if rd.missing:
            report.fail_reasons.append(
                f"doctor_report: не найдено {len(rd.missing)}/{rd.expected_count} "
                f"обязательных полей/пунктов: {rd.missing[:5]}"
            )

    if report.fail_reasons:
        report.status = "FAIL"

    er.print_report(report)

    assert report.classify_s < _CLASSIFY_BUDGET_S, (
        f"classify завис на {doc_path.name}: {report.classify_s:.1f}s")
    assert report.extract_s < _EXTRACT_BUDGET_S, (
        f"extract завис на {doc_path.name}: {report.extract_s:.1f}s")
    assert 0.0 <= classified.confidence <= 1.0
    assert not report.fail_reasons, f"{doc_path.name}: " + "; ".join(report.fail_reasons)
