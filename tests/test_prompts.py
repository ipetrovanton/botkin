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


def test_analysis_prompt_covers_one_sided_refs_and_value_text():
    p = prompts.ANALYSIS_VLM_SYSTEM
    assert "ref_operator" in p          # односторонние референсы описаны
    assert "<5.0" in p or "<" in p
    assert "value_text" in p
    assert "ref_text" in p


def test_lab_result_ignores_extra_fields():
    """Лишнее поле от модели не должно ломать парсинг (extra=ignore)."""
    from botkin.domain.models import LabResult
    m = LabResult.model_validate(
        {"analyte_name": "Глюкоза", "value_num": 5.4, "unit": "ммоль/л", "foo": "bar"}
    )
    assert m.analyte_name == "Глюкоза" and m.value_num == 5.4
