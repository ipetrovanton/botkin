"""Интеграционный тест парсинга бланка end-to-end по детерминированному пути.

Собираем реалистичный многостраничный бланк ОАК (как sample_020: одинокий C-реактивный
белок на отдельной странице + полная панель на второй) и прогоняем весь text-layer pipeline
run_analysis: reconstruct_pages → структурирование → completeness_guard → verbatim_guard.

LLM здесь не дёргаем — _structure_text подменяем детерминированным парсером строк
(_parse_text_line). Это изолирует тест от Ollama, но проходит через ту же сшивку, дедуп и
стражи, что и боевой путь. Заодно меряем скорость: детерминированный парс бланка обязан
укладываться в бюджет (это защита от случайной алгоритмической регрессии вроде O(n²)).
"""
import time

import pytest

import botkin.llm.extract as ex
from botkin.parsing.text_layer import _parse_text_line

# Полная панель ОАК с парой коллизий значений (0.6 vs 0.60), на которых ловился баг
# completeness_guard: float(0.6) == float(0.60). (name, value, unit, ref).
_CBC_ROWS = [
    ("Гематокрит", "40.8", "%", "35 - 45"),
    ("Гемоглобин", "13.7", "г/дл", "11.7 - 15.5"),
    ("Эритроциты", "4.64", "млн/мкл", "3.8 - 5.1"),
    ("MCV", "87.9", "фл", "81 - 100"),
    ("Тромбоциты", "217", "тыс/мкл", "150 - 400"),
    ("Лейкоциты", "5.15", "тыс/мкл", "4.5 - 11"),
    ("Базофилы", "0.6", "%", "< 1.0"),
    ("Моноциты", "0.60", "тыс/мкл", "0.2 - 0.95"),
    ("СОЭ", "9", "мм/ч", "< 20"),
]
_CRP_ROW = ("С-реактивный белок", "1.8", "мг/л", "<5.0")


def _structure_via_parser(lines, _name):
    """Детерминированная имитация LLM: размечает строки тем же парсером, что и страж."""
    return [r for ln in lines if (r := _parse_text_line(ln)) is not None]


def test_full_lab_pdf_parsed_end_to_end(make_lab_pdf, tmp_path, monkeypatch):
    pdf = tmp_path / "cbc.pdf"
    # rows_per_page=1 ставит C-реактивный белок одиноко на свою страницу — регресс sample_020.
    make_lab_pdf(pdf, [_CRP_ROW, *_CBC_ROWS], title="Общий анализ крови", rows_per_page=5)

    monkeypatch.setattr(ex, "_structure_text", _structure_via_parser)

    elapsed = -time.perf_counter()
    rows = ex.run_analysis(pdf)
    elapsed += time.perf_counter()

    by_name = {r.analyte_name: r for r in rows}
    expected = {"С-реактивный белок", *(name for name, *_ in _CBC_ROWS)}
    assert expected <= set(by_name), f"потеряны строки: {expected - set(by_name)}"

    # Коллизия значений 0.6 vs 0.60 — обе строки должны выжить раздельно.
    assert by_name["Базофилы"].value_num == 0.6
    assert by_name["Моноциты"].value_num == 0.6
    assert by_name["Базофилы"].value_raw != by_name["Моноциты"].value_raw

    # Скорость: детерминированный парс бланка << секунды. Порог щедрый — ловим только
    # грубые регрессии сложности, не флуктуации CI.
    print(f"\n[SPEED] run_analysis: {len(rows)} rows in {elapsed * 1000:.1f} ms")
    assert elapsed < 2.0, f"парсинг неожиданно медленный: {elapsed:.2f}s"


@pytest.mark.parametrize("n_rows", [50, 200])
def test_parsing_speed_scales_linearly(make_lab_pdf, tmp_path, monkeypatch, n_rows):
    """Бюджет скорости на крупном бланке — страховка от квадратичной деградации сшивки/дедупа."""
    rows_spec = [(f"Показатель{i}", f"{i % 90 + 1}.{i % 10}", "ед", "1 - 100")
                 for i in range(n_rows)]
    pdf = tmp_path / f"big_{n_rows}.pdf"
    make_lab_pdf(pdf, rows_spec, rows_per_page=20)

    monkeypatch.setattr(ex, "_structure_text", _structure_via_parser)

    elapsed = -time.perf_counter()
    rows = ex.run_analysis(pdf)
    elapsed += time.perf_counter()

    print(f"\n[SPEED] {n_rows} rows -> {len(rows)} parsed in {elapsed * 1000:.1f} ms")
    assert len(rows) == n_rows
    assert elapsed < 5.0, f"парсинг {n_rows} строк слишком медленный: {elapsed:.2f}s"
