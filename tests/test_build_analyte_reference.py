from scripts.build_analyte_reference import row_to_record, normalize_key, STATUS_MAP


HEADER = [
    "ID", "LOINC", "FULLNAME", "ENGLISHNAME", "SHORTNAME", "SYNONYMS", "ANALYTE",
    "SPECANALYTE", "MEASUREMENT", "UNIT", "SPECIMEN", "TIMECHAR", "METHODTYPE",
    "SCALETYPE", "TESTSTATUS", "GROUP", "NMU", "SORT",
]


def _row(**kw):
    d = {h: None for h in HEADER}
    d.update(kw)
    return d


def test_row_to_record_basic():
    rec = row_to_record(_row(
        LOINC="14979-9", FULLNAME="АЧТВ исследование", SHORTNAME="АЧТВ",
        ENGLISHNAME="APTT", SYNONYMS="АПТВ; Activated PTT", UNIT="с",
        GROUP="Коагулогические исследования", TESTSTATUS="Актуальный", NMU="A12.05.039",
    ))
    assert rec["name"] == "АЧТВ исследование"
    assert rec["short"] == "АЧТВ"
    assert rec["loinc"] == "14979-9"
    assert rec["nmu"] == "A12.05.039"
    assert rec["unit"] == "с"
    assert rec["group"] == "Коагулогические исследования"
    assert rec["status"] == "active"
    assert "АПТВ" in rec["synonyms"] and "Activated PTT" in rec["synonyms"]


def test_status_mapping():
    assert STATUS_MAP["Актуальный"] == "active"
    assert STATUS_MAP["Новый"] == "new"
    assert STATUS_MAP["Устаревший"] == "deprecated"


def test_loinc_zero_becomes_null():
    rec = row_to_record(_row(FULLNAME="Тест", LOINC="0", TESTSTATUS="Актуальный"))
    assert rec["loinc"] is None


def test_empty_fullname_skipped():
    assert row_to_record(_row(FULLNAME=None, TESTSTATUS="Актуальный")) is None


def test_normalize_key():
    assert normalize_key("  Гёмоглобин  Общий ") == "гемоглобин общий"


def _write_fsli_xlsx(path):
    """Мини-копия структуры ФСЛИ: лист «Справочник», стр.1 — машинная шапка,
    стр.2 — человекочитаемые описания, данные с стр.3."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Справочник"
    ws.append(HEADER)  # строка 1: ID, LOINC, FULLNAME, ...
    ws.append([          # строка 2: русские описания — НЕ данные
        "Уникальный идентификатор", "Код LOINC", "Полное наименование",
        "Английское наименование", "Краткое наименование", "Синонимы", "Аналит",
        "Характеристика аналита", "Размерность", "Единица измерения", "Образец",
        "Временная характеристика", "Тип метода", "Тип шкалы измерения", "Статус",
        "Группа тестов", "Код НМУ", "Порядок сортировки",
    ])
    row = {h: None for h in HEADER}
    row.update(LOINC="14979-9", FULLNAME="АЧТВ исследование", SHORTNAME="АЧТВ",
               UNIT="с", TESTSTATUS="Актуальный", NMU="A12.05.039")
    ws.append([row[h] for h in HEADER])  # строка 3: первая запись
    wb.save(path)


def test_build_registry_skips_description_row(tmp_path):
    from scripts.build_analyte_reference import build_registry

    xlsx = tmp_path / "fsli.xlsx"
    _write_fsli_xlsx(xlsx)
    records = build_registry(xlsx)
    # Ровно одна запись из строки 3; строка 2 («Полное наименование») не должна просочиться.
    assert len(records) == 1
    assert records[0]["name"] == "АЧТВ исследование"
    assert records[0]["loinc"] == "14979-9"
    names = {r["name"] for r in records}
    assert "Полное наименование" not in names
