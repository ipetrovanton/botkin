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
