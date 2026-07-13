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
