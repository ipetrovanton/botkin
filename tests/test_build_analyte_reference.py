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


def test_synonyms_split_on_semicolon_and_comma():
    """ФСЛИ смешивает разделители: 'A; B, C' → три синонима (771 запись в реестре)."""
    rec = row_to_record(_row(
        FULLNAME="АЧТВ в бедной тромбоцитами плазме", TESTSTATUS="Актуальный",
        SYNONYMS="Активированное время; APPT; aPTT, aPTT PPP, PTT",
    ))
    assert rec["synonyms"] == [
        "Активированное время", "APPT", "aPTT", "aPTT PPP", "PTT",
    ]


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


def _write_fsli_json(path, rows):
    """Мини-копия JSON-выгрузки ФСЛИ: {"records": [ {колонка: значение}, ... ]}."""
    import json

    records = []
    for kw in rows:
        d = {h: None for h in HEADER}
        d.update(kw)
        records.append(d)
    path.write_text(json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8")


def test_build_registry_reads_json(tmp_path):
    from scripts.build_analyte_reference import build_registry

    src = tmp_path / "fsli.json"
    _write_fsli_json(src, [
        dict(LOINC="14979-9", FULLNAME="АЧТВ исследование", SHORTNAME="АЧТВ",
             UNIT="с", TESTSTATUS="Актуальный", NMU="A12.05.039"),
    ])
    records = build_registry(src)
    assert len(records) == 1
    assert records[0]["name"] == "АЧТВ исследование"
    assert records[0]["loinc"] == "14979-9"


def test_build_registry_dedupes_by_name(tmp_path):
    """Дубль по нормализованному имени (ё→е, регистр) отбрасывается."""
    from scripts.build_analyte_reference import build_registry

    src = tmp_path / "fsli.json"
    _write_fsli_json(src, [
        dict(FULLNAME="Гемоглобин", TESTSTATUS="Актуальный", LOINC="718-7"),
        dict(FULLNAME="гёмоглобин", TESTSTATUS="Актуальный", LOINC="111-1"),
    ])
    records = build_registry(src)
    assert len(records) == 1
    assert records[0]["loinc"] == "718-7"  # первый победитель остаётся


# ── Дедуп-таблица по ANALYTE (подсказка имени/единиц, без биоматериала) ───────

def test_build_analyte_table_dedups_by_analyte(tmp_path):
    """Записи с одним ANALYTE, но разным биоматериалом/методом → одна запись.

    Имя = ANALYTE (без локуса), синонимы и единицы объединяются. FULLNAME/specimen
    НЕ сохраняются — биоматериал не подмешиваем в карточку.
    """
    from scripts.build_analyte_reference import build_analyte_table

    src = tmp_path / "fsli.json"
    _write_fsli_json(src, [
        dict(ANALYTE="Гемоглобин", SHORTNAME="Гемоглобин", SYNONYMS="Hb", UNIT="г/л",
             FULLNAME="Гемоглобин в крови", SPECIMEN="Кровь", TESTSTATUS="Актуальный"),
        dict(ANALYTE="Гемоглобин", SHORTNAME="Гемоглобин", SYNONYMS="HGB", UNIT="мг/л; мг/дл",
             FULLNAME="Гемоглобин в моче", SPECIMEN="Моча", TESTSTATUS="Актуальный"),
    ])
    table = build_analyte_table(src)
    assert len(table) == 1
    rec = table[0]
    assert rec["name"] == "Гемоглобин"
    assert "Hb" in rec["synonyms"] and "HGB" in rec["synonyms"]
    assert set(rec["units"]) >= {"г/л", "мг/л", "мг/дл"}  # UNIT расщепляется по ';'/','
    assert "specimen" not in rec and "fullname" not in rec  # биоматериал не храним


def test_build_analyte_table_skips_empty_analyte(tmp_path):
    from scripts.build_analyte_reference import build_analyte_table

    src = tmp_path / "fsli.json"
    _write_fsli_json(src, [
        dict(ANALYTE=None, FULLNAME="Что-то", TESTSTATUS="Актуальный"),
        dict(ANALYTE="Глюкоза", UNIT="ммоль/л", TESTSTATUS="Актуальный"),
    ])
    table = build_analyte_table(src)
    assert [r["name"] for r in table] == ["Глюкоза"]
