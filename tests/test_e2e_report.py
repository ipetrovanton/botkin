"""Юнит-тесты для модуля e2e_report (структурный diff и метрики E2E)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import e2e_report as er
from botkin.llm.metrics import InferenceMetrics


@dataclass
class _FakeRow:
    """Минимальный фейковый ряд для diff."""

    analyte_name: str
    value_num: float | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    value_text: str | None = None


def test_compare_analytes_finds_match_and_mismatch():
    expected = [
        {"name": "Гемоглобин", "value": 137.0, "unit": "г/л", "ref_low": 117.0, "ref_high": 155.0},
        {"name": "Лейкоциты", "value": 5.15, "unit": "10^9/л"},
    ]
    rows = [
        _FakeRow("Гемоглобин", 137.0, "г/л", 117.0, 155.0),
        _FakeRow("Лейкоциты", 5.15, "10^9/л"),
        _FakeRow("CRP", 5.0, "мг/л"),
    ]

    diff = er.compare_analytes(expected, rows)

    assert len(diff.matched) == 2
    assert len(diff.missing) == 0
    assert len(diff.extra) == 1
    assert diff.extra[0].analyte_name == "CRP"
    assert diff.precision == 2 / 3
    assert diff.recall == 1.0


def test_compare_analytes_reports_missing_and_unit_mismatch():
    expected = [
        {"name": "Гемоглобин", "value": 137.0, "unit": "г/л"},
        {"name": "Лейкоциты", "value": 5.15, "unit": "10^9/л"},
    ]
    rows = [
        _FakeRow("Гемоглобин", 137.0, "г/дл"),
        _FakeRow("Эритроциты", 4.6, "10^12/л"),
    ]

    diff = er.compare_analytes(expected, rows)

    assert len(diff.matched) == 1
    assert len(diff.missing) == 1
    assert diff.missing[0]["name"] == "Лейкоциты"
    assert len(diff.extra) == 1
    assert len(diff.field_mismatches) == 1
    assert diff.field_mismatches[0].field == "unit"


def test_compare_analytes_ref_mismatch():
    expected = [
        {"name": "Гемоглобин", "value": 137.0, "unit": "г/л", "ref_low": 117.0, "ref_high": 155.0},
    ]
    rows = [
        _FakeRow("Гемоглобин", 137.0, "г/л", 120.0, 150.0),
    ]

    diff = er.compare_analytes(expected, rows)

    assert len(diff.matched) == 1
    assert len(diff.field_mismatches) == 2
    assert {m.field for m in diff.field_mismatches} == {"ref_low", "ref_high"}


def test_metrics_capture_parses_log_metrics_output():
    logger = logging.getLogger("botkin.llm.metrics")
    logger.setLevel(logging.INFO)

    with er.MetricsCapture("botkin.llm.metrics") as capture:
        logger.info("[METRICS] model=qwen3-vl:8b | ctx=123/4096 | out=45 | tps=12.30 | t=2.10s")

    assert len(capture.records) == 1
    metrics = capture.records[0]
    assert metrics.model == "qwen3-vl:8b"
    assert metrics.prompt_tokens == 123
    assert metrics.num_ctx == 4096
    assert metrics.completion_tokens == 45
    assert metrics.tokens_per_second == 12.30
    assert metrics.elapsed_s == 2.10


def test_metrics_capture_skips_non_metrics_records():
    logger = logging.getLogger("botkin.llm.metrics")
    logger.setLevel(logging.INFO)

    with er.MetricsCapture("botkin.llm.metrics") as capture:
        logger.info("[START_EXTRACT] Doc: 'x' | Type: 'y' | Model: z")

    assert capture.records == []


def test_save_benchmark_writes_json(tmp_path):
    report = er.DocReport(
        name="sample.pdf",
        status="PASS",
        classify_s=1.2,
        extract_s=3.4,
        classify_metrics=[
            InferenceMetrics(
                model="qwen3-vl:8b",
                prompt_tokens=10,
                completion_tokens=5,
                elapsed_s=1.2,
                num_ctx=4096,
            ),
        ],
    )
    output = tmp_path / "e2e_report.json"

    er.save_benchmark([report], output, timestamp="2026-01-01T00:00:00")

    with output.open(encoding="utf-8") as f:
        data = json.load(f)

    assert data["timestamp"] == "2026-01-01T00:00:00"
    assert len(data["documents"]) == 1
    assert data["documents"][0]["name"] == "sample.pdf"
    assert data["documents"][0]["classify_s"] == 1.2
    assert data["documents"][0]["classify_metrics"][0]["model"] == "qwen3-vl:8b"
