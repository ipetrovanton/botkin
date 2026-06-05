from botkin.llm import prompts


def test_prompts_have_no_antithinking_cruft():
    all_text = " ".join(
        getattr(prompts, name) for name in dir(prompts)
        if name.endswith("_SYSTEM") and isinstance(getattr(prompts, name), str)
    )
    # instruct-вариант не уходит в thinking — костыли не нужны.
    assert "thinking" not in all_text.lower()
    assert "размышлени" not in all_text.lower()
    assert "```json" not in all_text   # structured output обеспечивает instructor


def test_core_prompts_present():
    assert prompts.CLASSIFY_VLM_SYSTEM
    assert prompts.ANALYSIS_VLM_SYSTEM
    assert prompts.DOCTOR_REPORT_VLM_SYSTEM
    assert not hasattr(prompts, "PRESCRIPTION_VLM_SYSTEM")   # рецепты сняты с поддержки


def test_analysis_prompt_describes_nested_schema():
    """Промпт согласован со схемой RawAnalysis: tests[].results[] c parameter/value/reference_range."""
    p = prompts.ANALYSIS_VLM_SYSTEM
    assert "test_name" in p and "tests" in p   # вложенная группировка по исследованиям
    assert "parameter" in p
    assert "value" in p
    assert "reference_range" in p


def test_lab_result_ignores_extra_fields():
    """Лишнее поле от модели не должно ломать парсинг (extra=ignore)."""
    from botkin.domain.models import LabResult
    m = LabResult.model_validate(
        {"analyte_name": "Глюкоза", "value_num": 5.4, "unit": "ммоль/л", "foo": "bar"}
    )
    assert m.analyte_name == "Глюкоза" and m.value_num == 5.4
