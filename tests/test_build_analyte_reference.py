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
