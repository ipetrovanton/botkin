"""Пост-обработка классификации по title и visible_text: корректировка явных ошибок VLM."""

from botkin.llm.classify import _correct_classification_by_content


def test_prescription_title_forces_unknown():
    assert _correct_classification_by_content("doctor_report", "РЕЦЕПТУРНЫЙ БЛАНК", None) == "unknown"
    assert _correct_classification_by_content("doctor_report", "Назначение препаратов", None) == "unknown"
    assert _correct_classification_by_content("doctor_report", "Рецепт", None) == "unknown"


def test_imaging_and_report_titles_force_doctor_report():
    assert _correct_classification_by_content("unknown", "МРТ головного мозга", None) == "doctor_report"
    assert _correct_classification_by_content("unknown", "Прием врача-невролога", None) == "doctor_report"
    assert _correct_classification_by_content("unknown", "Заключение", None) == "doctor_report"
    assert _correct_classification_by_content("unknown", "УЗИ брюшной полости", None) == "doctor_report"


def test_visible_text_can_override_wrong_title():
    # Модель дала title "Рецептурный бланк", но видимый текст — невролог.
    assert _correct_classification_by_content(
        "unknown", "Рецептурный бланк", "Прием врача-невролога"
    ) == "doctor_report"
    # Модель дала doctor_report, но видимый текст — рецепт.
    assert _correct_classification_by_content(
        "doctor_report", None, "Рецептурный бланк № 123"
    ) == "unknown"


def test_analysis_title_left_untouched():
    assert _correct_classification_by_content("analysis", "Общий анализ крови", None) == "analysis"


def test_no_content_left_untouched():
    assert _correct_classification_by_content("doctor_report", None, None) == "doctor_report"


def test_doctor_footer_on_lab_form_does_not_flip_analysis():
    # Регрессия sample_005 (бланк Тонус): VLM отдаёт analysis с conf 0.98, но в
    # visible_text попадает стандартный колонтитул с врачом — вердикт не должен меняться.
    assert _correct_classification_by_content(
        "analysis", "Специальные иммунологические исследования", "Врач(и): Гринева Л. П."
    ) == "analysis"
    assert _correct_classification_by_content(
        "analysis", None,
        "Интерпретация результатов исследования содержит информацию для лечащего врача",
    ) == "analysis"
    assert _correct_classification_by_content("analysis", None, "Ф.И.О. врача:") == "analysis"


def test_short_keyword_does_not_match_inside_word():
    # «кт» не должно находиться внутри «бактерии»/«лактат»/«фруктоза»,
    # иначе микробиологический бланк улетает в doctor_report.
    assert _correct_classification_by_content(
        "analysis", None, "Лактобактерии, фруктоза, лактат"
    ) == "analysis"
    # При этом самостоятельное «КТ» по-прежнему распознаётся.
    assert _correct_classification_by_content("unknown", "КТ органов грудной клетки", None) == "doctor_report"
