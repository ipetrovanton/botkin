"""Диагностика: почему completeness_guard пропускает СОЭ и pH мочи."""
import sys
from pathlib import Path

from botkin.preprocess.pdf_text import open_pdf
from botkin.parsing.text_layer import _parse_text_line, completeness_guard, _value_key

docs_dir = Path("tests/fixtures/documents/samples")

for name in ["sample_011.pdf", "sample_013.pdf"]:
    path = docs_dir / name
    print(f"\n{'='*70}")
    print(f"=== {name} ===")
    pdf = open_pdf(path)
    if not pdf.is_usable:
        print("  НЕТ текстового слоя")
        continue
    for pi, page in enumerate(pdf.pages):
        print(f"\n  --- Страница {pi+1} ({len(page)} строк) ---")
        for line in page:
            r = _parse_text_line(line)
            status = "OK" if r else "SKIP"
            if r:
                print(f"  [{status}] {line!r} -> {r.analyte_name!r} val={r.value_raw} ref={r.ref_text or f'{r.ref_low}-{r.ref_high}'}")
            else:
                # Показываем только строки, которые могут быть результатами
                # (содержат число и буквы)
                has_num = any(c.isdigit() for c in line)
                has_alpha = any(c.isalpha() for c in line)
                if has_num and has_alpha:
                    print(f"  [{status}] {line!r}")
