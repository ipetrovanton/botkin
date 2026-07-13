"""Диагностика OCR-текста для sample_011 — почему completeness_guard не находит СОЭ."""
import sys
from pathlib import Path

from botkin.llm.extract import _call_image_ocr, _structure_text
from botkin.parsing.text_layer import _parse_text_line, _parse_text_line_all, completeness_guard, _value_key
from botkin.preprocess.images import prepare_images, to_base64_jpegs
from botkin.config import IMAGE_EXTRACT_LONG_SIDE

doc = Path("tests/fixtures/documents/samples/sample_011.pdf")
images = prepare_images(doc, long_side=IMAGE_EXTRACT_LONG_SIDE, upscale=True, deskew=True, enhance=True)
b64 = to_base64_jpegs(images)

print("=== OCR-текст ===")
text = _call_image_ocr(b64, "sample_011.pdf")
print(text)
print(f"\n=== Длина: {len(text)} символов ===")

# Ищем СОЭ в OCR-тексте
print("\n=== Строки с СОЭ/ESR/скорость ===")
for line in text.splitlines():
    if any(kw in line.lower() for kw in ["соэ", "esr", "скорость осед"]):
        print(f"  {line!r}")
        r = _parse_text_line(line)
        print(f"  _parse_text_line: {r}")
        all_r = _parse_text_line_all(line)
        print(f"  _parse_text_line_all: {[(r.analyte_name, r.value_raw, r.ref_text) for r in all_r]}")

# Структурируем
print("\n=== Структурирование ===")
rows = _structure_text(text.splitlines(), "sample_011.pdf")
print(f"Строк: {len(rows)}")
for r in rows:
    print(f"  {r.analyte_name!r} val={r.value_raw} ref={r.ref_text or f'{r.ref_low}-{r.ref_high}'}")

# completeness_guard
print("\n=== completeness_guard ===")
recovered = completeness_guard(text.splitlines(), rows)
print(f"Добрано: {len(recovered)}")
for r in recovered:
    print(f"  {r.analyte_name!r} val={r.value_raw} ref={r.ref_text or f'{r.ref_low}-{r.ref_high}'}")
