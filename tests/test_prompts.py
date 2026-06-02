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
    assert prompts.PRESCRIPTION_VLM_SYSTEM
    assert prompts.DOCTOR_REPORT_VLM_SYSTEM
