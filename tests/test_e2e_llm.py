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
import dataclasses
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from botkin.config import VLM_MODEL
from botkin.llm import classify
from botkin.llm import extract as ex
from botkin.llm.client import _detect_ollama_url, _is_url_reachable
from botkin.normalize.units import canonical_unit

# Отчёт печатает единицы с надстрочными символами (10⁹/л, 10¹²/л). При запуске через
# WSL python.exe с выводом в cp1251-консоль PowerShell такой символ роняет print
# (UnicodeEncodeError). errors="replace" заменяет некодируемое на '?', не теряя поток.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):  # поток без reconfigure (перехвачен/перенаправлен)
    pass

# Потолки времени щедрые: одиночный VLM-запрос ограничен VLM_REQUEST_TIMEOUT (120 с),
# extract может сделать несколько вызовов. Цель ассерта — поймать зависание, не мерить железо.
_CLASSIFY_BUDGET_S = 180.0
_EXTRACT_BUDGET_S = 900.0

_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def _ollama_skip_reason() -> str | None:
    """None если Ollama доступна и нужная модель загружена; иначе причина для skip."""
    url = _detect_ollama_url()
    if not _is_url_reachable(url):
        return f"Ollama недоступна по {url} — пропускаю e2e (нужен запущенный Ollama в WSL2)"
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as resp:
            tags = json.load(resp)
    except (OSError, ValueError) as e:
        return f"не удалось прочитать список моделей Ollama ({url}/api/tags): {e}"

    names = {m.get("name", "") for m in tags.get("models", [])}
    base = VLM_MODEL.split(":", 1)[0]
    has_model = any(n == VLM_MODEL or n.split(":", 1)[0] == base for n in names)
    if not has_model:
        return f"модель {VLM_MODEL} не загружена в Ollama (есть: {sorted(names)}); выполните `ollama pull {VLM_MODEL}`"
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


@dataclasses.dataclass
class _Mismatch:
    """Одно несовпадение по полю сопоставленного показателя (unit/ref_low/ref_high)."""
    analyte: str
    field: str
    expected: object
    got: object


@dataclasses.dataclass
class _DocReport:
    """Полный отчёт по одному документу: тайминги, тип, сверка значений и полей."""
    name: str
    status: str = "PASS"                       # PASS | FAIL | SKIP
    fail_reasons: list[str] = dataclasses.field(default_factory=list)
    classify_s: float = 0.0
    extract_s: float = 0.0
    doc_type_expected: str | None = None
    doc_type_got: str | None = None
    expected_values: int = 0
    extracted_rows: int = 0
    matched_values: int = 0
    missing: list[dict] = dataclasses.field(default_factory=list)   # эталонные analytes без пары
    field_mismatches: list[_Mismatch] = dataclasses.field(default_factory=list)

    @property
    def total_s(self) -> float:
        return self.classify_s + self.extract_s


# Собираем отчёты всех параметризованных запусков для итоговой сводки в конце модуля.
_REPORTS: list[_DocReport] = []


def _units_equal(expected: str | None, got: str | None) -> bool:
    """Единицы равны после канонизации (млн/мкл vs 10^6/мкл — формат, не суть)."""
    if not expected or not got:
        return True   # нечего сравнивать — не считаем расхождением
    return canonical_unit(expected)[0] == canonical_unit(got)[0] or expected.strip() == got.strip()


def _compare_analytes(rows, expected_analytes):
    """Сопоставляет эталонные analytes с извлечёнными строками по значению (1:1, мультимножество).

    Сверяем числа, а не имена: VLM варьирует формулировки ("MCV" / "MCV (ср. объём эритр.)"),
    но значение объективно. Для каждой найденной пары дополнительно сверяем unit/ref_low/ref_high
    — это информативные расхождения, не валящие тест (форматирование/округление эталона варьирует).

    Возвращает (matched_pairs, missing, field_mismatches).
    """
    remaining = list(rows)
    matched_pairs: list[tuple[dict, object]] = []
    missing: list[dict] = []
    for analyte in expected_analytes:
        value = analyte.get("value")
        if value is None:
            continue                          # качественный результат — не сверяем по числу
        key = round(value, 2)
        candidates = [i for i, r in enumerate(remaining)
                      if r.value_num is not None and round(r.value_num, 2) == key]
        if not candidates:
            missing.append(analyte)
        else:
            # Если кандидатов несколько (СИБР — повторяющиеся значения O2/CH4),
            # выбираем по наибольшей близости имени, а не первого попавшегося.
            idx = max(candidates, key=lambda i: _name_score(analyte.get("name", ""), remaining[i].analyte_name))
            matched_pairs.append((analyte, remaining.pop(idx)))

    field_mismatches: list[_Mismatch] = []
    for analyte, row in matched_pairs:
        name = analyte.get("name", "?")
        if not _units_equal(analyte.get("unit"), row.unit):
            field_mismatches.append(_Mismatch(name, "unit", analyte.get("unit"), row.unit))
        for ref_field in ("ref_low", "ref_high"):
            exp_ref = analyte.get(ref_field)
            got_ref = getattr(row, ref_field)
            if exp_ref is not None and got_ref is not None and abs(exp_ref - got_ref) > 0.01:
                field_mismatches.append(_Mismatch(name, ref_field, exp_ref, got_ref))
    return matched_pairs, missing, field_mismatches


def _name_score(expected_name: str, got_name: str) -> float:
    """Близость имён: точное совпадение токенов + специальная обработка СИБР."""
    expected_lower = expected_name.lower().replace("ё", "е")
    got_lower = got_name.lower().replace("ё", "е")

    # СИБР: ключевые токены — время (число + "минут") и газ (H2/CH4/H2+2CH4/O2).
    if "сибр" in expected_lower:
        time_match = re.search(r"(\d+)\s*минут", expected_lower)
        gas = next((g for g in ("h2+2ch4", "ch4", "h2", "o2") if g in expected_lower), None)
        if time_match and gas:
            time_ok = time_match.group(1) in got_lower
            gas_ok = gas in got_lower
            if time_ok and gas_ok:
                return 1.0
            if time_ok or gas_ok:
                return 0.5

    # Общий случай: доля пересечения токенов.
    expected_tokens = set(expected_lower.split())
    got_tokens = set(got_lower.split())
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & got_tokens) / len(expected_tokens)


def _print_report(report: _DocReport, rows) -> None:
    """Печатает подробный отчёт по документу: тайминги, тип, сверка значений и полей."""
    print(f"\n{'=' * 70}")
    print(f"[E2E] {report.name} — {report.status}")
    print(f"  Время:    classify {report.classify_s:.1f}s | "
          f"extract {report.extract_s:.1f}s | всего {report.total_s:.1f}s")
    type_mark = "OK" if report.doc_type_got == report.doc_type_expected else "MISMATCH"
    print(f"  Тип:      получ '{report.doc_type_got}' / эталон '{report.doc_type_expected}' [{type_mark}]")
    if report.expected_values:
        print(f"  Значения: совпало {report.matched_values}/{report.expected_values} "
              f"(извлечено строк: {report.extracted_rows})")
    if report.missing:
        print(f"  НЕ НАЙДЕНЫ эталонные значения ({len(report.missing)}):")
        for analyte in report.missing:
            unit = f" {analyte['unit']}" if analyte.get("unit") else ""
            print(f"    - {analyte.get('name', '?')}: {analyte.get('value')}{unit}")
        print("  Извлечено моделью:")
        for row in rows:
            unit = f" {row.unit}" if row.unit else ""
            print(f"    · {row.analyte_name}: {row.value_num}{unit}")
    if report.field_mismatches:
        print(f"  Расхождения по unit/ref ({len(report.field_mismatches)}):")
        for mismatch in report.field_mismatches:
            print(f"    ~ {mismatch.analyte} [{mismatch.field}]: "
                  f"эталон {mismatch.expected} / получ {mismatch.got}")


pytestmark = pytest.mark.llm


@pytest.fixture(scope="module", autouse=True)
def _require_ollama():
    reason = _ollama_skip_reason()
    if reason:
        pytest.skip(reason, allow_module_level=True)


@pytest.fixture(scope="module", autouse=True)
def _print_summary():
    """После всех документов печатает сводную таблицу: статус, тайминги, точность значений."""
    yield
    if not _REPORTS:
        return
    print(f"\n\n{'#' * 78}")
    print("ИТОГОВАЯ СВОДКА E2E (по всем документам)")
    print("#" * 78)
    header = f"{'Документ':<22}{'Статус':<7}{'classify':>9}{'extract':>9}{'всего':>8}{'значения':>11}"
    print(header)
    print("-" * 78)
    total_classify = total_extract = 0.0
    passed = failed = 0
    for r in sorted(_REPORTS, key=lambda x: x.name):
        total_classify += r.classify_s
        total_extract += r.extract_s
        passed += r.status == "PASS"
        failed += r.status == "FAIL"
        values = f"{r.matched_values}/{r.expected_values}" if r.expected_values else "—"
        print(f"{r.name:<22}{r.status:<7}{r.classify_s:>8.1f}s{r.extract_s:>8.1f}s"
              f"{r.total_s:>7.1f}s{values:>11}")
    print("-" * 78)
    total_s = total_classify + total_extract
    print(f"Документов: {len(_REPORTS)} | PASS: {passed} | FAIL: {failed}")
    print(f"Время: classify {total_classify:.1f}s | extract {total_extract:.1f}s | "
          f"всего {total_s:.1f}s ({total_s / 60:.1f} мин)")
    if _REPORTS:
        print(f"Среднее на документ: {total_s / len(_REPORTS):.1f}s")
    fails = [r for r in _REPORTS if r.status == "FAIL"]
    if fails:
        print("\nПРОВАЛЫ:")
        for r in fails:
            print(f"  {r.name}: {'; '.join(r.fail_reasons)}")
    print("#" * 78)


def test_e2e_synthetic_cbc_classified_and_extracted(make_lab_pdf, tmp_path):
    """Синтетический бланк ОАК: реальный classify+extract, сверка с ground-truth + замер."""
    pdf = tmp_path / "e2e_cbc.pdf"
    make_lab_pdf(pdf, _CBC_GROUND_TRUTH, lab="ИНВИТРО",
                 title="Общий анализ крови", rows_per_page=20)

    t = -time.perf_counter()
    classified = classify.run_vlm(pdf)
    t += time.perf_counter()
    print(f"\n[SPEED] classify (синтетика): {t:.1f}s -> {classified.doc_type} (conf={classified.confidence:.2f})")
    assert t < _CLASSIFY_BUDGET_S, f"classify завис: {t:.1f}s"
    assert classified.doc_type == "analysis", f"бланк ОАК классифицирован как {classified.doc_type}"

    t = -time.perf_counter()
    rows = ex.run_analysis(pdf)
    t += time.perf_counter()
    print(f"[SPEED] extract (синтетика): {t:.1f}s -> {len(rows)} строк")
    assert t < _EXTRACT_BUDGET_S, f"extract завис: {t:.1f}s"

    # Полнота: большинство эталонных показателей должно найтись (допускаем расхождения
    # в написании имени от модели, поэтому сверяем по нормализованному вхождению).
    found = " ".join(r.analyte_name.lower() for r in rows)
    expected_names = [name for name, *_ in _CBC_GROUND_TRUTH]
    hits = [name for name in expected_names if name.lower()[:5] in found]
    print(f"[E2E] распознано {len(hits)}/{len(expected_names)} эталонных показателей")
    assert len(hits) >= len(expected_names) * 0.7, (
        f"распознано лишь {len(hits)}/{len(expected_names)}: {[r.analyte_name for r in rows]}")


@pytest.mark.parametrize("doc_path", _discover_documents() or [None],
                         ids=lambda p: p.name if p else "no-documents")
def test_e2e_real_document_pipeline(doc_path):
    """Реальный документ из BOTKIN_E2E_DOCS_DIR: classify+extract с замером по этапам и
    детальной сверкой против sidecar-эталона (doc_type, значения, unit/ref)."""
    if doc_path is None:
        pytest.skip(
            f"нет реальных документов в {_docs_dir()}; задайте BOTKIN_E2E_DOCS_DIR "
            "или положите PDF/фото в tests/fixtures/documents")

    expected = _load_expected(doc_path) or {}
    report = _DocReport(name=doc_path.name, doc_type_expected=expected.get("doc_type"))
    _REPORTS.append(report)
    rows = []

    # Этап 1: классификация (с fast-path по текстовому слою — см. classify.run_vlm).
    t = -time.perf_counter()
    classified = classify.run_vlm(doc_path)
    report.classify_s = t + time.perf_counter()
    report.doc_type_got = classified.doc_type

    if classified.doc_type == "analysis":
        # Этап 2: извлечение показателей (препроцессинг + VLM/текстовый слой внутри).
        t = -time.perf_counter()
        rows = ex.run_analysis(doc_path)
        report.extract_s = t + time.perf_counter()
        report.extracted_rows = len(rows)

    # Сверка типа документа.
    if report.doc_type_expected and report.doc_type_got != report.doc_type_expected:
        report.fail_reasons.append(
            f"doc_type: получено '{report.doc_type_got}', ожидался '{report.doc_type_expected}'")

    # Сверка значений показателей (только для анализов с sidecar-разметкой).
    analytes = expected.get("analytes") or []
    if classified.doc_type == "analysis" and analytes:
        matched, missing, field_mismatches = _compare_analytes(rows, analytes)
        report.expected_values = sum(1 for a in analytes if a.get("value") is not None)
        report.matched_values = len(matched)
        report.missing = missing
        report.field_mismatches = field_mismatches
        if missing:
            report.fail_reasons.append(
                f"не найдено {len(missing)}/{report.expected_values} эталонных значений")

    if report.fail_reasons:
        report.status = "FAIL"

    _print_report(report, rows)

    # Ассерты-потолки скорости — ловят зависшую/деградировавшую модель.
    assert report.classify_s < _CLASSIFY_BUDGET_S, f"classify завис на {doc_path.name}: {report.classify_s:.1f}s"
    assert report.extract_s < _EXTRACT_BUDGET_S, f"extract завис на {doc_path.name}: {report.extract_s:.1f}s"
    assert 0.0 <= classified.confidence <= 1.0
    # Жёсткие ассерты на корректность — после печати отчёта, чтобы причина была видна.
    assert not report.fail_reasons, f"{doc_path.name}: " + "; ".join(report.fail_reasons)
