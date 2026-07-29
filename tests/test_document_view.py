"""P2: рендер карточки документа через публичный контракт compose_card.

Было: тесты тащили _format_labs/_format_ref через реэкспорт из handlers.show
и проверяли приватные хелперы. Стало: публичный document_view.compose_card
проверяется с подменой LabRepo/get_conn, чтобы не трогать БД.
"""
from __future__ import annotations

import pytest

from botkin.bot import document_view


def _doc(**kw) -> dict:
    return {
        "id": 1,
        "user_id": 999,
        "doc_type": "analysis",
        "status": "extracted",
        "clinic": "Клиника",
        "created_at": "2026-01-01T00:00:00",
        **kw,
    }


def _row(**kw) -> dict:
    base = {
        "analyte_name": "X",
        "value_num": None,
        "value_text": None,
        "unit": None,
        "ref_low": None,
        "ref_high": None,
        "ref_operator": None,
        "ref_text": None,
        "analyte_canonical": None,
        "loinc": None,
        "nmu_code": None,
        "analyte_group": None,
        "match_status": None,
        "unit_expected": None,
        "unit_mismatch": None,
    }
    base.update(kw)
    return base


@pytest.fixture
def patch_labs(monkeypatch):
    """Подменяет LabRepo и get_conn в document_view под заданный набор строк."""
    rows_ref = {"rows": []}

    class FakeLabRepo:
        def __init__(self, conn, user_id):
            pass

        def for_document(self, doc_id):
            return rows_ref["rows"]

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(document_view, "LabRepo", FakeLabRepo)
    monkeypatch.setattr(document_view, "get_conn", lambda: FakeConn())

    def _apply(rows: list[dict]) -> None:
        rows_ref["rows"] = rows

    return _apply


def test_compose_card_has_header_separator_and_details(patch_labs):
    patch_labs([])
    text = document_view.compose_card(7, _doc(id=7, doc_type="unknown"))
    assert "Документ #7" in text
    assert "────────────" in text
    assert "не поддерживается" in text


def test_compose_card_text_result_rendered_not_none(patch_labs):
    patch_labs([_row(analyte_name="Антитела", value_text="не обнаружено")])
    text = document_view.compose_card(1, _doc())
    assert "не обнаружено" in text
    assert "None" not in text


def test_compose_card_one_sided_ref_shown(patch_labs):
    patch_labs([_row(analyte_name="СРБ", value_num=1.8, unit="мг/л",
                     ref_operator="<", ref_high=5.0)])
    text = document_view.compose_card(1, _doc())
    assert "1.8" in text
    assert "&lt;5.0" in text


def test_compose_card_ref_operator_html_escaped(patch_labs):
    # «< 1.0» (базофилы) ломал Telegram parse_mode=HTML: '<1.0' читался как тег.
    patch_labs([_row(analyte_name="Базофилы", value_num=0.6, unit="%",
                     ref_operator="<", ref_high=1.0)])
    text = document_view.compose_card(1, _doc())
    assert "&lt;1.0" in text
    assert "<1.0" not in text


def test_compose_card_two_sided_ref_and_high_marker(patch_labs):
    patch_labs([_row(analyte_name="Глюкоза", value_num=7.0, unit="ммоль/л",
                     ref_low=3.9, ref_high=6.1)])
    text = document_view.compose_card(1, _doc())
    assert "3.9" in text and "6.1" in text and "⬆️" in text


def test_compose_card_low_marker_with_operator_ref(patch_labs):
    # value ниже нижней границы ">120"
    patch_labs([_row(analyte_name="X", value_num=100.0,
                     ref_operator=">", ref_low=120.0)])
    text = document_view.compose_card(1, _doc())
    assert "⬇️" in text


def test_compose_card_text_ref_shown(patch_labs):
    patch_labs([_row(analyte_name="HBsAg", value_text="отрицательно",
                     ref_text="отрицательно")])
    text = document_view.compose_card(1, _doc())
    assert "отрицательно" in text


def test_compose_card_unit_mismatch_warning(patch_labs):
    patch_labs([_row(analyte_name="Глюкоза", value_num=5.4, unit="г/л",
                     unit_expected="ммоль/л", unit_mismatch=1)])
    text = document_view.compose_card(1, _doc())
    assert "⚠️" in text


def test_compose_card_empty_rows(patch_labs):
    patch_labs([])
    text = document_view.compose_card(1, _doc())
    assert text.endswith("—")


def test_compose_card_rows_numbered_not_bulleted(patch_labs):
    patch_labs([
        _row(analyte_name="Гемоглобин", value_num=140.0, unit="г/л"),
        _row(analyte_name="Глюкоза", value_num=5.4, unit="ммоль/л"),
    ])
    text = document_view.compose_card(1, _doc())
    lines = text.splitlines()
    assert lines[-2].startswith("1. ") and "Гемоглобин" in lines[-2]
    assert lines[-1].startswith("2. ") and "Глюкоза" in lines[-1]
    assert "•" not in text


def test_compose_card_numbering_counts_only_shown_rows(patch_labs):
    # строка без значения пропускается — нумерация идёт по реально показанным
    patch_labs([
        _row(analyte_name="Пусто"),
        _row(analyte_name="Гемоглобин", value_num=140.0),
    ])
    text = document_view.compose_card(1, _doc())
    lines = text.splitlines()
    assert lines[-1].startswith("1. ") and "Гемоглобин" in lines[-1]
    assert "2." not in text


def test_compose_card_ref_variants(patch_labs):
    patch_labs([
        _row(analyte_name="A", value_num=1.0, ref_low=3.9, ref_high=6.1),
        _row(analyte_name="B", value_num=1.0, ref_operator="<", ref_high=5.0),
        _row(analyte_name="C", value_num=1.0, ref_operator=">", ref_low=120.0),
        _row(analyte_name="D", value_text="отриц", ref_text="отриц"),
        _row(analyte_name="E", value_num=1.0),
    ])
    text = document_view.compose_card(1, _doc())
    assert "норма 3.9" in text and "6.1" in text
    assert "&lt;5.0" in text
    assert "&gt;120.0" in text
    assert "норма: отриц" in text
    assert "1. " in text
