"""Unit-тесты early-exit voting для Андрофлор-OCR."""
from botkin.domain.models import LabResult
from botkin.llm import androflor_ocr as af


def _rows(n: int) -> list[LabResult]:
    return [LabResult(analyte_name=f"sp_{i}", value_num=float(i), unit="Lg") for i in range(n)]


def test_androflor_voting_skips_when_initial_enough(monkeypatch):
    """Если первичный OCR уже дал ≥ MIN строк — дополнительных вызовов нет."""
    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("OCR не должен вызываться")

    monkeypatch.setattr(af, "call_image_ocr", _boom)
    out = af.androflor_voting([], "doc", _rows(af._ANDROFLOR_MIN_ROWS))
    assert len(out) == af._ANDROFLOR_MIN_ROWS
    assert calls["n"] == 0


def test_androflor_voting_early_exits_on_full_table(monkeypatch):
    """Первая же vote-попытка с полной таблицей — дальше не ходим."""
    calls = {"n": 0}

    def _ocr(*_a, **_k):
        calls["n"] += 1
        # parse_androflor_ocr вызовем через monkeypatch ниже — вернём фиктивный текст
        return "fake"

    def _parse(_text):
        return _rows(af._ANDROFLOR_FULL_ROWS)

    monkeypatch.setattr(af, "call_image_ocr", _ocr)
    monkeypatch.setattr(af, "parse_androflor_ocr", _parse)
    out = af.androflor_voting([], "doc", _rows(0))
    assert len(out) == af._ANDROFLOR_FULL_ROWS
    assert calls["n"] == 1  # early-exit после первой успешной


def test_androflor_voting_continues_until_tries_if_partial(monkeypatch):
    """Неполный набор — крутим все попытки, берём максимум строк."""
    calls = {"n": 0}

    def _ocr(*_a, **_k):
        calls["n"] += 1
        return "fake"

    def _parse(_text):
        # 5, 8, 12 — все < FULL, растёт
        n = {1: 5, 2: 8, 3: 12}[calls["n"]]
        return _rows(n)

    monkeypatch.setattr(af, "call_image_ocr", _ocr)
    monkeypatch.setattr(af, "parse_androflor_ocr", _parse)
    out = af.androflor_voting([], "doc", _rows(0))
    assert len(out) == 12
    assert calls["n"] == af._ANDROFLOR_VOTING_TRIES
