"""Unit-тесты модели достоверного прогресса (pipeline/progress_model.py)."""
import sqlite3

import pytest

from botkin.pipeline.progress_model import (
    ProgressEstimate,
    StageDurationStore,
    estimate_progress,
)


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return StageDurationStore(conn)


class TestStageDurationStore:
    def test_expected_returns_default_without_history(self, store):
        assert store.expected("recognizing") == 25.0
        assert store.expected("normalizing") == 75.0

    def test_record_creates_ema(self, store):
        store.record("recognizing", 40.0)
        assert store.expected("recognizing") == 40.0

    def test_ema_smooths_updates(self, store):
        store.record("recognizing", 40.0)
        store.record("recognizing", 100.0)
        # EMA: 40 + 0.3 * (100 - 40) = 58
        assert store.expected("recognizing") == pytest.approx(58.0)

    def test_unknown_stage_fallback(self, store):
        assert store.expected("nonexistent") == 30.0


class TestEstimateProgress:
    def test_failed_is_dead(self, store):
        est = estimate_progress("failed", None, store)
        assert est.alive is False
        assert est.percent == 0

    def test_extracted_is_100(self, store):
        est = estimate_progress("extracted", None, store)
        assert est.percent == 100
        assert est.eta_seconds == 0
        assert est.alive is True

    def test_received_starts_near_zero(self, store):
        now = 1000.0
        est = estimate_progress("received", now, store, now=now)
        assert 0 <= est.percent <= 5
        assert est.alive is True

    def test_progress_grows_within_stage(self, store):
        start = 1000.0
        early = estimate_progress("normalizing", start, store, now=start + 5)
        later = estimate_progress("normalizing", start, store, now=start + 50)
        assert later.percent > early.percent

    def test_progress_never_reaches_100_before_extracted(self, store):
        start = 1000.0
        # Прошло в 10 раз больше ожидаемого — процент всё равно < 100
        est = estimate_progress("normalizing", start, store, now=start + 750)
        assert est.percent < 100

    def test_percent_monotonic_across_stages(self, store):
        now = 1000.0
        recognizing_end = estimate_progress("recognizing", now - 1000, store, now=now)
        normalizing_start = estimate_progress("normalizing", now, store, now=now)
        # Начало следующей стадии не откатывает процент назад
        assert normalizing_start.percent >= recognizing_end.percent - 5

    def test_eta_decreases_over_time(self, store):
        start = 1000.0
        early = estimate_progress("recognizing", start, store, now=start + 1)
        later = estimate_progress("recognizing", start, store, now=start + 20)
        assert later.eta_seconds <= early.eta_seconds

    def test_no_stage_started_at_gives_stage_base(self, store):
        est = estimate_progress("normalizing", None, store)
        # received (0.02) + recognizing (0.28) = 30%
        assert est.percent == 30
        assert est.alive is True

    def test_eta_includes_future_stages(self, store):
        start = 1000.0
        est = estimate_progress("recognizing", start, store, now=start)
        # ETA >= остаток recognizing (25s) + normalizing (75s)
        assert est.eta_seconds >= 90
