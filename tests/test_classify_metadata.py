from botkin.domain.models import ClassifyResult


def test_classify_result_has_metadata_fields():
    r = ClassifyResult(doc_type="analysis", confidence=0.9,
                        title="Общий анализ мочи", clinic="Инвитро")
    assert r.title == "Общий анализ мочи"
    assert r.clinic == "Инвитро"


def test_classify_result_metadata_optional():
    r = ClassifyResult(doc_type="unknown", confidence=0.5)
    assert r.title is None
    assert r.clinic is None
