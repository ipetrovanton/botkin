from botkin.normalize.analytes import AnalyteNormalizer


def _rec(name, short=None, english=None, synonyms=(), loinc=None, nmu=None,
         unit=None, group=None, status="active"):
    return {"name": name, "short": short, "english": english,
            "synonyms": list(synonyms), "loinc": loinc, "nmu": nmu,
            "unit": unit, "group": group, "status": status}


def _norm(records=None):
    records = records or [
        _rec("Гемоглобин", short="HGB", synonyms=["Hb"], loinc="718-7",
             nmu="B03.016.003", unit="г/л", group="Гематологические исследования"),
        _rec("Глюкоза", short="GLU", english="Glucose", loinc="2345-7",
             unit="ммоль/л", group="Биохимические исследования"),
        _rec("С-реактивный белок", short="СРБ", synonyms=["CRP"], loinc="1988-5",
             unit="мг/л", group="Биохимические исследования"),
    ]
    return AnalyteNormalizer(records)


def test_exact_match():
    m = _norm().correct("Гемоглобин")
    assert m.canonical == "Гемоглобин" and m.status == "matched" and m.distance == 0
    assert m.loinc == "718-7" and m.nmu == "B03.016.003" and m.expected_unit == "г/л"


def test_ocr_typo_corrected():
    m = _norm().correct("Глюкоэа")          # OCR з→э
    assert m.canonical == "Глюкоза" and m.status == "matched"


def test_match_by_synonym():
    assert _norm().correct("CRP").canonical == "С-реактивный белок"


def test_match_by_short_form():
    assert _norm().correct("СРБ").canonical == "С-реактивный белок"


def test_short_abbreviation_requires_exact():
    # «HGB» точно совпадает с короткой формой
    assert _norm().correct("HGB").canonical == "Гемоглобин"
    # случайные 3 буквы не должны прилепляться к короткой форме
    assert _norm().correct("XYZ").status == "unverified"


def test_unknown_not_snapped():
    m = _norm().correct("Неведомыйпоказательксено")
    assert m.status == "unverified" and m.canonical is None
    assert m.raw == "Неведомыйпоказательксено"


def test_raw_preserved():
    assert _norm().correct("Глюкоэа").raw == "Глюкоэа"


def test_status_carried():
    n = _norm([_rec("Старый тест", status="deprecated")])
    assert n.correct("Старый тест").match_status == "deprecated"


def test_loader_reads_packaged_registry():
    from botkin.normalize.analytes import load_default
    n = load_default()
    assert n.correct("Гемоглобин").canonical is not None
    assert n.correct("Глюкоза").status == "matched"
