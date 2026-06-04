"""Verbatim-страж: каждое число строки обязано присутствовать в исходном тексте слоя."""
from botkin.llm.extract import _verbatim_guard
from botkin.domain.models import LabResult

SOURCE = "Гемоглобин 13.7 г/дл 11.7 - 15.5\nЭритроциты 4.64 млн/мкл 3.8 - 5.1"


def test_guard_keeps_row_present_in_source():
    rows = [LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                      ref_low=11.7, ref_high=15.5)]
    kept, rejected = _verbatim_guard(rows, SOURCE)
    assert len(kept) == 1 and len(rejected) == 0


def test_guard_rejects_hallucinated_value():
    # 137 и 120/160 отсутствуют в исходном тексте — галлюцинация-нормализация.
    rows = [LabResult(analyte_name="Гемоглобин", value_num=137.0, value_raw="137",
                      ref_low=120.0, ref_high=160.0)]
    kept, rejected = _verbatim_guard(rows, SOURCE)
    assert len(kept) == 0 and len(rejected) == 1


def test_guard_handles_comma_decimal_and_integer_ref():
    rows = [LabResult(analyte_name="Эритроциты", value_num=4.64, value_raw="4,64",
                      ref_low=3.8, ref_high=5.1)]
    kept, _ = _verbatim_guard(rows, SOURCE)  # 4,64 == 4.64 в источнике
    assert len(kept) == 1
