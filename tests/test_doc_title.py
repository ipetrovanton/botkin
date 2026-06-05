"""Обобщённый заголовок документа по группе исследований ФСЛИ (биоматериал не используем)."""
from botkin.normalize.analytes import summary_title


def test_summary_title_majority_group():
    # ОАК — большинство показателей гематологические → заголовок по группе
    title = summary_title([
        "Гематологические исследования",
        "Гематологические исследования",
        "Биохимические исследования",
    ])
    assert title == "Гематологические исследования"


def test_summary_title_fallback_to_test_name():
    # группа не определена → берём название исследования
    title = summary_title([None, None], test_names=["Микроэлементы волос"])
    assert title == "Микроэлементы волос"


def test_summary_title_final_fallback():
    assert summary_title([], test_names=[]) == "Лабораторные анализы"
    assert summary_title([None], test_names=[None]) == "Лабораторные анализы"
