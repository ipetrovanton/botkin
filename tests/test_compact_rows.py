"""Разбор компактного построчного ответа модели «имя|значение|единица|референс»."""

from botkin.parsing.rows import parse_compact_rows, rows_from_raw


def test_parses_four_field_lines():
    raw = parse_compact_rows(
        "Гемоглобин|13.7|г/дл|11.7 - 15.5\n"
        "СРБ|<1.0|мг/л|\n"
    )
    rows = rows_from_raw(raw)
    assert [r.analyte_name for r in rows] == ["Гемоглобин", "СРБ"]
    assert rows[0].value_num == 13.7
    assert rows[0].unit == "г/дл"
    assert rows[0].ref_low == 11.7
    assert rows[0].ref_high == 15.5
    assert rows[1].unit == "мг/л"


def test_missing_trailing_fields_are_optional():
    rows = rows_from_raw(parse_compact_rows("Ферритин|79.9"))
    assert len(rows) == 1
    assert rows[0].unit is None
    assert rows[0].ref_text is None


def test_markdown_table_noise_is_tolerated():
    # Модель иногда всё-таки оборачивает вывод в markdown-таблицу.
    rows = rows_from_raw(parse_compact_rows(
        "| имя | значение | единица | референс |\n"
        "|---|---|---|---|\n"
        "| Глюкоза | 5.1 | ммоль/л | 4.1 - 5.9 |\n"
    ))
    assert [r.analyte_name for r in rows] == ["Глюкоза"]
    assert rows[0].value_num == 5.1


def test_lines_without_value_are_skipped():
    # Заголовки групп и пояснения не должны становиться показателями.
    rows = rows_from_raw(parse_compact_rows(
        "Клинический анализ крови|\n"
        "Лейкоциты|6.2|10^9/л|4.0 - 9.0\n"
        "текст без разделителя\n"
    ))
    assert [r.analyte_name for r in rows] == ["Лейкоциты"]


def test_empty_input_gives_no_rows():
    assert rows_from_raw(parse_compact_rows("")) == []
    assert rows_from_raw(parse_compact_rows("совсем не таблица")) == []


def test_group_name_column_is_dropped():
    """Регрессия sample_012/013: модель добавляла колонку исследования, поля уезжали вправо."""
    rows = rows_from_raw(parse_compact_rows(
        "ОБЩИЙ АНАЛИЗ МОЧИ|Лейкоциты (микроскопия)|1|в п/зр.|< 5\n"
        "ОБЩИЙ АНАЛИЗ МОЧИ|Эпителий переходный|не обнар|в п/зр.|< 1\n"
    ))
    assert [r.analyte_name for r in rows] == ["Лейкоциты (микроскопия)", "Эпителий переходный"]
    assert rows[0].value_num == 1.0
    assert rows[0].unit == "в п/зр."
    assert rows[1].value_text == "не обнар"


def test_normal_four_field_line_is_not_shifted():
    """Обычная строка не должна пострадать от защиты выше: имя остаётся первым полем."""
    rows = rows_from_raw(parse_compact_rows("Гемоглобин|13.7|г/дл|11.7 - 15.5"))
    assert rows[0].analyte_name == "Гемоглобин"
    assert rows[0].value_num == 13.7


def test_value_flags_are_preserved_verbatim():
    rows = rows_from_raw(parse_compact_rows("Нейтрофилы, %|44.6*|%|47 - 72"))
    assert rows[0].value_raw == "44.6*"
    assert rows[0].value_num == 44.6


def test_ref_mistakenly_in_unit_field_is_recovered():
    """Регрессия sample_011: модель писала референс в колонку unit, unit пустой."""
    rows = rows_from_raw(parse_compact_rows(
        "Лейкоциты|4.14|4 - 8,8|\n"
        "Гемоглобин|153|118 - 168|\n"
    ))
    assert rows[0].value_num == 4.14
    assert rows[0].unit is None
    assert rows[0].ref_low == 4.0
    assert rows[0].ref_high == 8.8
    assert rows[1].ref_low == 118.0
    assert rows[1].ref_high == 168.0


def test_swapped_unit_and_ref_are_reordered():
    """unit=диапазон, ref=единица → меняем местами."""
    rows = rows_from_raw(parse_compact_rows("Тромбоциты|164|150 - 400|10^9/л"))
    assert rows[0].unit == "10^9/л"
    assert rows[0].ref_low == 150.0
    assert rows[0].ref_high == 400.0


def test_trailing_slash_on_unit_is_stripped():
    rows = rows_from_raw(parse_compact_rows("Гематокрит|40.8|%/|35 - 45"))
    assert rows[0].unit == "%"


def test_noise_filter_drops_headers_keeps_analytes():
    from botkin.domain.models import LabResult
    from botkin.parsing.rows import filter_noise_rows

    rows = [
        LabResult(analyte_name="Исследование", value_text="—"),
        LabResult(analyte_name="Лейкоциты::", value_num=4.14, unit="10^9/л"),
        LabResult(analyte_name="—", value_num=4.8),
        LabResult(analyte_name="Гемоглобин", value_num=137.0, unit="г/л"),
    ]
    out = filter_noise_rows(rows)
    assert [r.analyte_name for r in out] == ["Лейкоциты", "Гемоглобин"]


def test_dedup_collapses_trailing_colon_names():
    from botkin.domain.models import LabResult
    from botkin.parsing.rows import dedup_rows

    rows = dedup_rows([
        LabResult(analyte_name="Лейкоциты", value_num=4.14),
        LabResult(analyte_name="Лейкоциты::", value_num=4.14),
    ])
    assert len(rows) == 1
