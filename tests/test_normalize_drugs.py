from botkin.normalize.drugs import DrugNormalizer


def _rec(name, type="trade", mnn=None, statuses=("active",), reg_numbers=()):
    return {"name": name, "type": type, "mnn": mnn,
            "statuses": list(statuses), "reg_numbers": list(reg_numbers)}


def _norm(records=None):
    # Небольшой набор записей в памяти — не тянем полный справочник в юнит-тест.
    records = records or [
        _rec("Элькар", mnn="Левокарнитин", statuses=["modified"], reg_numbers=["ЛСР-006143/10"]),
        _rec("Глиатилин", mnn="Холина альфосцерат", statuses=["active", "eaeu"]),
        _rec("Флуоксетин", type="both", mnn="Флуоксетин", statuses=["active", "excluded"]),
        _rec("Триттико", mnn="Тразодон", statuses=["eaeu"]),
        _rec("Аторвастатин", type="mnn"),
    ]
    return DrugNormalizer(records)   # параметры из config (Дамерау-cap + ratio-floor)


def test_corrects_misread_names():
    n = _norm()
    assert n.correct("элкап").canonical == "Элькар"        # dist=2
    assert n.correct("элкап").status == "matched"
    assert n.correct("глиалатин").canonical == "Глиатилин"  # dist=3 (транспозиция)
    assert n.correct("Флюоксетин").canonical == "Флуоксетин"
    assert n.correct("тритико").canonical == "Триттико"


def test_match_carries_mnn_and_statuses():
    m = _norm().correct("элкап")
    assert m.mnn == "Левокарнитин"                          # заполнение МНН из торгового
    assert "modified" in m.statuses
    assert m.reg_numbers == ("ЛСР-006143/10",)


def test_exact_match_zero_distance():
    m = _norm().correct("аторвастатин")
    assert m.canonical == "Аторвастатин" and m.distance == 0


def test_preserves_raw_always():
    assert _norm().correct("Элкап").raw == "Элкап"          # оригинал не теряется


def test_unknown_drug_not_snapped():
    match = _norm().correct("ксенобластомицинпрепарат")
    assert match.status == "unverified"
    assert match.canonical is None
    assert match.raw == "ксенобластомицинпрепарат"


def test_ratio_floor_rejects_within_cap_but_dissimilar():
    n = _norm([_rec("Парацетамол", type="mnn")])
    assert n.correct("кофе").status == "unverified"


def test_free_text_strips_dose_tail():
    # doctor_report.medications — строка с дозой/формой.
    m = _norm().correct_free_text("Элкап - 300 мг/мл (питьевая форма) по 2,5 мл")
    assert m.canonical == "Элькар"


def test_loader_reads_packaged_registry():
    from botkin.normalize.drugs import load_default
    n = load_default()
    # В registry.jsonl (ГРЛС) есть «Элькар»/«Глиатилин» — мисриды чинятся при дефолтном config.
    assert n.correct("элкап").canonical == "Элькар"
    assert n.correct("Глиалатин").canonical == "Глиатилин"
    assert n.correct("элкап").mnn == "Левокарнитин"
