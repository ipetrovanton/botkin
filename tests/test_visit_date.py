"""Тесты парсера даты исследования (не дата рождения)."""
from botkin.llm.visit_date import extract_visit_date_from_text, report_text_blob
from botkin.domain.models import DoctorReport


def test_extracts_labeled_study_date_not_birth():
    text = """
    Ф.И.О. пациента: Петров Антон Игоревич
    Дата рождения: 24.02.1993
    Область исследования: головной мозг
    Дата исследования: 17.11.2024
    Магнитно-резонансная томография
    """
    dt = extract_visit_date_from_text(text)
    assert dt is not None
    assert dt.date().isoformat() == "2024-11-17"


def test_extracts_priem_label():
    text = "Дата приёма: 23.03.2026\nДиагноз: G90.8"
    dt = extract_visit_date_from_text(text)
    assert dt is not None
    assert dt.day == 23 and dt.month == 3 and dt.year == 2026


def test_skips_birth_date_only():
    text = "Дата рождения: 24.02.1993\nПациент обследован."
    assert extract_visit_date_from_text(text) is None


def test_report_text_blob_joins_fields():
    r = DoctorReport(
        diagnosis="Заключение: норма",
        anamnesis="Дата исследования: 17.11.2024",
        recommendations=["Консультация невролога"],
    )
    blob = report_text_blob(r)
    assert "17.11.2024" in blob
    assert "невролога" in blob
