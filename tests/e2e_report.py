"""E2E-отчёты и структурный diff для test_e2e_llm.py.

Модуль вынесен из самого теста, чтобы:
  * diff был переиспользуемым и unit-тестируемым;
  * отчёт можно было сохранять в benchmarks/ (Фаза 6.3);
  * инференс-метрики ([METRICS] из botkin.llm.metrics) захватывались и
    выводились в едином формате.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from botkin.llm.metrics import InferenceMetrics
from botkin.normalize.units import canonical_unit

log = logging.getLogger(__name__)

# Формат, который печатает log_metrics(...) в botkin.llm.metrics.
# Пример: "[METRICS] Doc: 'sample.pdf' model=qwen3-vl:8b | ctx=123/4096 | out=45 | tps=12.3 | t=2.10s"
_METRICS_RE = re.compile(
    r"^\[METRICS\]"
    r"(?:\s+Doc:\s*'(?P<doc>[^']*)')?"
    r"\s*model=(?P<model>[^\s|]+)"
    r"\s*\|\s*ctx=(?P<ctx>[^\s|]+)"
    r"\s*\|\s*out=(?P<out>\d+)"
    r"\s*\|\s*tps=(?P<tps>[^\s|]+)"
    r"\s*\|\s*t=(?P<t>[\d.]+)s$"
)


@dataclass(frozen=True)
class FieldMismatch:
    """Одно расхождение по полю сопоставленного показателя (unit/ref)."""

    analyte: str
    field: str
    expected: object
    got: object


@dataclass
class AnalyteDiff:
    """Структурный diff по показателям: matched, missing, extra, mismatched."""

    matched: list[tuple[dict, object]] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    extra: list[object] = field(default_factory=list)
    field_mismatches: list[FieldMismatch] = field(default_factory=list)

    @property
    def expected_count(self) -> int:
        return len(self.matched) + len(self.missing)

    @property
    def actual_count(self) -> int:
        return len(self.matched) + len(self.extra)

    @property
    def precision(self) -> float:
        actual = self.actual_count
        if actual == 0:
            return 0.0
        return len(self.matched) / actual

    @property
    def recall(self) -> float:
        expected = self.expected_count
        if expected == 0:
            return 0.0
        return len(self.matched) / expected


@dataclass
class DocReport:
    """Итог по одному документу: тайминг, тип, diff, метрики инференса."""

    name: str
    status: str = "PASS"
    fail_reasons: list[str] = field(default_factory=list)
    classify_s: float = 0.0
    extract_s: float = 0.0
    classify_metrics: list[InferenceMetrics] = field(default_factory=list)
    extract_metrics: list[InferenceMetrics] = field(default_factory=list)
    doc_type_expected: str | None = None
    doc_type_got: str | None = None
    diff: AnalyteDiff = field(default_factory=AnalyteDiff)

    @property
    def total_s(self) -> float:
        return self.classify_s + self.extract_s

    @property
    def expected_values(self) -> int:
        return self.diff.expected_count

    @property
    def extracted_rows(self) -> int:
        return self.diff.actual_count

    @property
    def matched_values(self) -> int:
        return len(self.diff.matched)

    @property
    def missing(self) -> list[dict]:
        return self.diff.missing

    @property
    def field_mismatches(self) -> list[FieldMismatch]:
        return self.diff.field_mismatches


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_metrics_message(text: str) -> InferenceMetrics | None:
    """Превратить строку log_metrics(...) обратно в InferenceMetrics."""
    match = _METRICS_RE.match(text.strip())
    if match is None:
        return None

    model = match.group("model")
    raw_ctx = match.group("ctx")
    out = _to_int(match.group("out"))
    tps_raw = match.group("tps")
    elapsed_s = _to_float(match.group("t")) or 0.0

    if "/" in raw_ctx:
        prompt_tokens = _to_int(raw_ctx.split("/", 1)[0])
        num_ctx = _to_int(raw_ctx.split("/", 1)[1])
    else:
        prompt_tokens = _to_int(raw_ctx)
        num_ctx = None

    tokens_per_second = None if tps_raw == "n/a" else _to_float(tps_raw)

    return InferenceMetrics(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=out,
        elapsed_s=elapsed_s,
        num_ctx=num_ctx,
        tokens_per_second=tokens_per_second,
    )


class _MetricsLogHandler(logging.Handler):
    """Handler, захватывающий все [METRICS]-записи лога."""

    def __init__(self, records: list[InferenceMetrics]) -> None:
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        metrics = _parse_metrics_message(record.getMessage())
        if metrics is not None:
            self._records.append(metrics)


class MetricsCapture:
    """Контекстный менеджер для захвата метрик из лога на время LLM-вызова."""

    def __init__(self, logger_name: str = "botkin.llm.metrics") -> None:
        self.logger = logging.getLogger(logger_name)
        self.records: list[InferenceMetrics] = []
        self._handler = _MetricsLogHandler(self.records)

    def __enter__(self) -> MetricsCapture:
        self.logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc: object) -> None:
        self.logger.removeHandler(self._handler)


def _units_equal(expected: str | None, got: str | None) -> bool:
    """Единицы равны после канонизации (млн/мкл vs 10^6/мкл — формат, не суть)."""
    if not expected or not got:
        return True
    return canonical_unit(expected)[0] == canonical_unit(got)[0] or expected.strip() == got.strip()


def _name_score(expected_name: str, got_name: str) -> float:
    """Близость имён: точное совпадение токенов + специальная обработка СИБР."""
    expected_lower = expected_name.lower().replace("ё", "е")
    got_lower = got_name.lower().replace("ё", "е")

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

    expected_tokens = set(expected_lower.split())
    got_tokens = set(got_lower.split())
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & got_tokens) / len(expected_tokens)


def _row_value(row: object) -> float | None:
    return getattr(row, "value_num", None)


def _row_name(row: object) -> str:
    return getattr(row, "analyte_name", "?") or "?"


def _row_unit(row: object) -> str | None:
    return getattr(row, "unit", None)


def _row_ref(row: object, field: str) -> float | None:
    return getattr(row, field, None)


def compare_analytes(expected_analytes: list[dict], rows: list[object]) -> AnalyteDiff:
    """Структурный diff: matched, missing, extra, расхождения по полям.

    Сверяем числа, а не имена: VLM варьирует формулировки, но значение объективно.
    """
    remaining = list(rows)
    matched: list[tuple[dict, object]] = []
    missing: list[dict] = []

    for analyte in expected_analytes:
        value = analyte.get("value")
        if value is None:
            continue

        key = round(_to_float(value) or 0.0, 2)
        candidates = [
            i for i, row in enumerate(remaining)
            if _row_value(row) is not None and round(_row_value(row), 2) == key
        ]
        if not candidates:
            missing.append(analyte)
        else:
            idx = max(
                candidates,
                key=lambda i: _name_score(analyte.get("name", ""), _row_name(remaining[i])),
            )
            matched.append((analyte, remaining.pop(idx)))

    field_mismatches: list[FieldMismatch] = []
    for analyte, row in matched:
        name = analyte.get("name", "?")
        if not _units_equal(analyte.get("unit"), _row_unit(row)):
            field_mismatches.append(FieldMismatch(name, "unit", analyte.get("unit"), _row_unit(row)))

        for ref_field in ("ref_low", "ref_high"):
            expected_ref = _to_float(analyte.get(ref_field))
            got_ref = _row_ref(row, ref_field)
            if expected_ref is not None and got_ref is not None and abs(expected_ref - got_ref) > 0.01:
                field_mismatches.append(FieldMismatch(name, ref_field, expected_ref, got_ref))

    return AnalyteDiff(
        matched=matched,
        missing=missing,
        extra=remaining,
        field_mismatches=field_mismatches,
    )


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    return str(value)


def _format_row(row: object) -> str:
    value = _row_value(row)
    unit = _row_unit(row)
    name = _row_name(row)
    if value is None:
        return f"{name}: {_format_value(getattr(row, 'value_text', None))}"
    suffix = f" {unit}" if unit else ""
    return f"{name}: {value}{suffix}"


def _format_analyte(analyte: dict) -> str:
    value = analyte.get("value")
    unit = analyte.get("unit")
    name = analyte.get("name", "?")
    if value is None:
        return f"{name}: {_format_value(analyte.get('value_text'))}"
    suffix = f" {unit}" if unit else ""
    return f"{name}: {value}{suffix}"


def _aggregate_metrics(records: list[InferenceMetrics]) -> dict[str, Any]:
    """Суммарные prompt/completion/tokens-per-second по списку метрик."""
    if not records:
        return {}

    prompt_tokens = sum(r.prompt_tokens for r in records)
    completion_tokens = sum(r.completion_tokens for r in records)
    elapsed_s = sum(r.elapsed_s for r in records)
    valid_tps = [r for r in records if r.tokens_per_second is not None]
    if valid_tps:
        total_completion = sum(r.completion_tokens for r in valid_tps)
        if total_completion:
            weighted_tps = (
                sum(r.tokens_per_second * r.completion_tokens for r in valid_tps) / total_completion
            )
        else:
            weighted_tps = 0.0
    else:
        weighted_tps = 0.0

    models = sorted({r.model for r in records})
    num_ctx_values = sorted({r.num_ctx for r in records if r.num_ctx is not None})
    return {
        "model": "; ".join(models) if len(models) > 1 else models[0],
        "num_ctx": ",".join(str(c) for c in num_ctx_values) if num_ctx_values else None,
        "calls": len(records),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "elapsed_s": elapsed_s,
        "tokens_per_second": round(weighted_tps, 2) if weighted_tps else None,
    }


def _format_metrics_line(records: list[InferenceMetrics]) -> str:
    if not records:
        return "n/a"
    agg = _aggregate_metrics(records)
    tps = f"{agg['tokens_per_second']:.1f}" if agg.get("tokens_per_second") else "n/a"
    ctx = (
        f"{agg['prompt_tokens']}/{agg['num_ctx']}"
        if agg.get("num_ctx")
        else str(agg["prompt_tokens"])
    )
    return (
        f"model={agg['model']} | ctx={ctx} | out={agg['completion_tokens']} "
        f"| tps={tps} | t={agg['elapsed_s']:.2f}s (calls={agg['calls']})"
    )


def _print_analyte_diff(diff: AnalyteDiff, file: TextIO) -> None:
    if diff.missing:
        print(f"  MISSING ({len(diff.missing)}):", file=file)
        for analyte in diff.missing:
            print(f"    - {_format_analyte(analyte)}", file=file)

    if diff.extra:
        print(f"  EXTRA ({len(diff.extra)}):", file=file)
        for row in diff.extra:
            print(f"    + {_format_row(row)}", file=file)

    if diff.field_mismatches:
        print(f"  MISMATCH ({len(diff.field_mismatches)}):", file=file)
        for mismatch in diff.field_mismatches:
            print(
                f"    ~ {mismatch.analyte} parameter={mismatch.field}: "
                f"expected={_format_value(mismatch.expected)} got={_format_value(mismatch.got)}",
                file=file,
            )


def print_report(report: DocReport, file: TextIO | None = None) -> None:
    """Подробный diff по одному документу."""
    if file is None:
        file = sys.stdout

    print(f"\n{'=' * 70}", file=file)
    print(f"[E2E] {report.name} — {report.status}", file=file)
    print(
        f"  Время:    classify {report.classify_s:.1f}s | "
        f"extract {report.extract_s:.1f}s | всего {report.total_s:.1f}s",
        file=file,
    )
    print(f"  Метрики:  classify: {_format_metrics_line(report.classify_metrics)}", file=file)
    print(f"            extract:  {_format_metrics_line(report.extract_metrics)}", file=file)

    type_mark = "OK" if report.doc_type_got == report.doc_type_expected else "MISMATCH"
    print(
        f"  Тип:      получ '{report.doc_type_got}' / "
        f"эталон '{report.doc_type_expected}' [{type_mark}]",
        file=file,
    )

    if report.expected_values:
        print(
            f"  Значения: precision={report.diff.precision:.2f} "
            f"recall={report.diff.recall:.2f} | "
            f"совпало {report.matched_values}/{report.expected_values} "
            f"(извлечено: {report.extracted_rows})",
            file=file,
        )
        _print_analyte_diff(report.diff, file)

    if report.fail_reasons:
        print(f"  Причины:  {'; '.join(report.fail_reasons)}", file=file)


def print_summary(reports: list[DocReport], file: TextIO | None = None) -> None:
    """Сводная таблица по всем документам с метриками и diff-статистикой."""
    if file is None:
        file = sys.stdout

    if not reports:
        return

    print(f"\n\n{'#' * 78}", file=file)
    print("ИТОГОВАЯ СВОДКА E2E", file=file)
    print("#" * 78, file=file)

    header = (
        f"{'Документ':<22}"
        f"{'Статус':<7}"
        f"{'classify':>9}"
        f"{'extract':>9}"
        f"{'всего':>8}"
        f"{'precision':>10}"
        f"{'recall':>8}"
        f"{'mismatch':>9}"
    )
    print(header, file=file)
    print("-" * 78, file=file)

    total_classify = total_extract = 0.0
    passed = failed = 0
    for report in sorted(reports, key=lambda r: r.name):
        total_classify += report.classify_s
        total_extract += report.extract_s
        passed += report.status == "PASS"
        failed += report.status == "FAIL"

        precision = f"{report.diff.precision:.2f}" if report.expected_values else "—"
        recall = f"{report.diff.recall:.2f}" if report.expected_values else "—"
        mismatch = f"{len(report.field_mismatches)}" if report.expected_values else "—"

        print(
            f"{report.name:<22}"
            f"{report.status:<7}"
            f"{report.classify_s:>8.1f}s"
            f"{report.extract_s:>8.1f}s"
            f"{report.total_s:>7.1f}s"
            f"{precision:>10}"
            f"{recall:>8}"
            f"{mismatch:>9}",
            file=file,
        )

    print("-" * 78, file=file)
    total_s = total_classify + total_extract
    print(
        f"Документов: {len(reports)} | PASS: {passed} | FAIL: {failed}",
        file=file,
    )
    print(
        f"Время: classify {total_classify:.1f}s | extract {total_extract:.1f}s | "
        f"всего {total_s:.1f}s ({total_s / 60:.1f} мин)",
        file=file,
    )
    if reports:
        print(f"Среднее на документ: {total_s / len(reports):.1f}s", file=file)

    fails = [r for r in reports if r.status == "FAIL"]
    if fails:
        print("\nПРОВАЛЫ:", file=file)
        for report in fails:
            print(f"  {report.name}: {'; '.join(report.fail_reasons)}", file=file)
    print("#" * 78, file=file)


def save_benchmark(
    reports: list[DocReport],
    output_path: Path,
    timestamp: str | None = None,
) -> None:
    """Сохранить E2E-отчёт в JSON для benchmarks/."""
    if timestamp is None:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    def _metrics_to_dict(metrics: InferenceMetrics) -> dict:
        return dataclasses.asdict(metrics)

    def _report_to_dict(report: DocReport) -> dict:
        return {
            "name": report.name,
            "status": report.status,
            "fail_reasons": report.fail_reasons,
            "classify_s": round(report.classify_s, 3),
            "extract_s": round(report.extract_s, 3),
            "total_s": round(report.total_s, 3),
            "classify_metrics": [_metrics_to_dict(m) for m in report.classify_metrics],
            "extract_metrics": [_metrics_to_dict(m) for m in report.extract_metrics],
            "doc_type_expected": report.doc_type_expected,
            "doc_type_got": report.doc_type_got,
            "precision": report.diff.precision,
            "recall": report.diff.recall,
            "expected_values": report.expected_values,
            "extracted_rows": report.extracted_rows,
            "matched_values": report.matched_values,
            "missing_count": len(report.missing),
            "extra_count": len(report.diff.extra),
            "field_mismatch_count": len(report.field_mismatches),
            "missing": [{
                "name": a.get("name"),
                "value": a.get("value"),
                "unit": a.get("unit"),
            } for a in report.missing],
            "field_mismatches": [{
                "analyte": m.analyte,
                "field": m.field,
                "expected": m.expected,
                "got": m.got,
            } for m in report.field_mismatches],
        }

    payload = {
        "timestamp": timestamp,
        "documents": [_report_to_dict(r) for r in reports],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
