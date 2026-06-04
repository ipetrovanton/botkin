from botkin.normalize.analytes import AnalyteNormalizer


def _rec(name, synonyms=(), units=(), group=None):
    """Запись дедуп-таблицы по ANALYTE: имя + синонимы + набор единиц (без биоматериала)."""
    return {"name": name, "synonyms": list(synonyms), "units": list(units), "group": group}


def _norm(records=None):
    records = records or [
        _rec("Гемоглобин", synonyms=["HGB", "Hb"], units=["г/л"],
             group="Гематологические исследования"),
        _rec("Глюкоза", synonyms=["GLU", "Glucose"], units=["ммоль/л"],
             group="Биохимические исследования"),
        _rec("С-реактивный белок", synonyms=["СРБ", "CRP"], units=["мг/л"],
             group="Биохимические исследования"),
    ]
    return AnalyteNormalizer(records)


def test_exact_match():
    m = _norm().correct("Гемоглобин")
    assert m.canonical == "Гемоглобин" and m.status == "matched" and m.distance == 0
    assert "г/л" in m.expected_units and m.group == "Гематологические исследования"


def test_canonical_is_clean_analyte_not_fullname():
    # Имя из справочника — чистый ANALYTE, без биоматериала/метода.
    m = _norm().correct("Гемоглобин")
    assert m.canonical == "Гемоглобин"
    assert "крови" not in m.canonical and "моче" not in m.canonical


def test_ocr_typo_corrected():
    m = _norm().correct("Глюкоэа")          # OCR з→э
    assert m.canonical == "Глюкоза" and m.status == "matched"


def test_match_by_synonym():
    assert _norm().correct("CRP").canonical == "С-реактивный белок"


def test_match_by_short_form():
    assert _norm().correct("СРБ").canonical == "С-реактивный белок"


def test_short_abbreviation_requires_exact():
    assert _norm().correct("HGB").canonical == "Гемоглобин"
    assert _norm().correct("XYZ").status == "unverified"


def test_unknown_not_snapped():
    m = _norm().correct("Неведомыйпоказательксено")
    assert m.status == "unverified" and m.canonical is None
    assert m.raw == "Неведомыйпоказательксено"


def test_raw_preserved():
    assert _norm().correct("Глюкоэа").raw == "Глюкоэа"


def test_multiple_units_collected():
    n = _norm([_rec("Гемоглобин", units=["г/л", "ммоль/л"])])
    m = n.correct("Гемоглобин")
    assert set(m.expected_units) == {"г/л", "ммоль/л"}


def test_canonical_name_wins_over_other_synonym():
    # «Тромбоциты» — точное имя одного показателя и синоним другого (CD31+клетки).
    # Точное каноническое имя должно победить, даже если запись-вор идёт раньше.
    n = AnalyteNormalizer([
        {"name": "CD31+клетки", "synonyms": ["тромбоциты", "моноциты"], "units": ["%"]},
        {"name": "Тромбоциты", "synonyms": [], "units": ["10^9/л"]},
    ])
    assert n.correct("Тромбоциты").canonical == "Тромбоциты"


def test_real_registry_platelets_not_cd31():
    from botkin.normalize.analytes import load_default
    assert load_default().correct("Тромбоциты").canonical == "Тромбоциты"


def test_loader_reads_packaged_registry():
    from botkin.normalize.analytes import load_default
    n = load_default()
    assert n.correct("Гемоглобин").canonical is not None
    assert n.correct("Глюкоза").status == "matched"
