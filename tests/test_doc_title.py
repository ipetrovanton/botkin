"""Обобщённый заголовок документа по биоматериалу (specimen из ФСЛИ)."""
from botkin.normalize.analytes import specimen_category, summary_title


def test_specimen_category_blood():
    for sp in ["Кровь венозная", "Сыворотка крови", "Плазма крови"]:
        assert specimen_category(sp) == "Анализ крови"


def test_specimen_category_urine_and_others():
    assert specimen_category("Моча") == "Анализ мочи"
    assert specimen_category("Кал") == "Анализ кала"
    assert specimen_category("Слюна") == "Анализ слюны"


def test_specimen_category_unknown():
    assert specimen_category("Волос") is None
    assert specimen_category(None) is None
    assert specimen_category("") is None


def test_summary_title_majority_specimen():
    # Документ СРБ + ОАК — оба кровь → «Анализ крови»
    title = summary_title(["Сыворотка крови", "Кровь венозная", "Кровь венозная"])
    assert title == "Анализ крови"


def test_summary_title_mixed_takes_majority():
    title = summary_title(["Моча", "Кровь венозная", "Кровь венозная"])
    assert title == "Анализ крови"


def test_summary_title_fallback_to_test_name():
    # биоматериал не распознан → берём название исследования из бланка
    title = summary_title(["Волос", None], test_names=["Микроэлементы волос"])
    assert title == "Микроэлементы волос"


def test_summary_title_final_fallback():
    assert summary_title([], test_names=[]) == "Лабораторные анализы"
    assert summary_title([None], test_names=[None]) == "Лабораторные анализы"
