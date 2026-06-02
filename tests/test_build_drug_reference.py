import io
import zipfile

import openpyxl

from scripts.build_drug_reference import (
    build_registry, is_meaningful_name, normalize_key, status_of,
)


def test_status_of_maps_sheet_titles():
    assert status_of("Действующий") == "active"
    assert status_of("Исключённый") == "excluded"
    assert status_of("Приостановлено применение") == "suspended"


def test_normalize_key_and_meaningful():
    assert normalize_key(" Глиатилин ") == "глиатилин"
    assert normalize_key("Тёма") == "тема"            # ё→е
    assert is_meaningful_name("Элькар")
    assert not is_meaningful_name("12")               # нет кириллицы
    assert not is_meaningful_name("ок")               # короче 3


def _make_grls_zip(tmp_path, rows, sheet_title="Действующий"):
    """Мини-XLSX в формате ГРЛС: шапка в первых 6 строках, данные с 7-й (индекс 6)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    for _ in range(6):
        ws.append([None] * 17)
    for reg, trade, mnn in rows:
        row = [None] * 17
        row[2], row[8], row[9] = reg, trade, mnn
        ws.append(row)
    xlsx = io.BytesIO()
    wb.save(xlsx)
    path = tmp_path / "grls.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("grls-Действующий.xlsx", xlsx.getvalue())
    return path


def test_build_registry_extracts_structured_record(tmp_path):
    path = _make_grls_zip(tmp_path, [("ЛП-1", "Глиатилин", "Холина альфосцерат")])
    registry = build_registry(path)

    trade = registry["глиатилин"]
    assert trade["types"] == {"trade"}
    assert trade["name"] == "Глиатилин"
    assert trade["mnn"] == "Холина альфосцерат"
    assert trade["statuses"] == {"active"}
    assert trade["reg_numbers"] == {"ЛП-1"}
    # МНН индексируется отдельной записью
    assert "холина альфосцерат" in registry


def test_build_registry_skips_rows_without_reg_or_name(tmp_path):
    path = _make_grls_zip(tmp_path, [(None, "Глиатилин", "X"), ("ЛП-2", None, "Y")])
    assert build_registry(path) == {}
