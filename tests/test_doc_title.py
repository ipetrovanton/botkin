"""Обобщённый заголовок документа по группе исследований ФСЛИ (биоматериал не используем)."""
from botkin.normalize.analytes import is_cbc_analyte, is_cbc_panel, summary_title


def test_summary_title_recognizes_cbc_panel():
    # В реестре ФСЛИ показатели ОАК разбросаны по группам (Гемоглобин/Эритроциты/Лейкоциты —
    # «Химико-микроскопические»), и голосование по группам даёт мусорный заголовок.
    # По СОСТАВУ панели это однозначно общий анализ крови.
    names = ["Гематокрит", "Гемоглобин", "Эритроциты", "Тромбоциты", "Лейкоциты",
             "Нейтрофилы", "СОЭ"]
    groups = ["Химико-микроскопические исследования"] * 5 + \
             ["Гематологические исследования", "Гематологические исследования"]
    assert summary_title(groups, test_names=names) == "Общий анализ крови"


def test_summary_title_not_cbc_for_unrelated_panel():
    # Единичный гематологический показатель в биохимической панели — не ОАК.
    names = ["Глюкоза", "Холестерин", "Гемоглобин гликированный"]
    groups = ["Биохимические исследования"] * 3
    assert summary_title(groups, test_names=names) == "Биохимические исследования"


def test_is_cbc_analyte_membership():
    # Имена показателей ОАК (в т.ч. с квалификаторами/аббревиатуры) → True.
    assert is_cbc_analyte("Гемоглобин")
    assert is_cbc_analyte("MCHC (ср. конц. Hb в эр.)")
    assert is_cbc_analyte("СОЭ")
    # Чужой показатель → False.
    assert not is_cbc_analyte("Глюкоза")


def test_is_cbc_panel_threshold():
    assert is_cbc_panel(["Гемоглобин", "Эритроциты", "Лейкоциты"])
    assert not is_cbc_panel(["Гемоглобин", "Глюкоза"])


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
