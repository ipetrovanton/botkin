"""Проверка multi-result парсинга на склеенных строках."""
from botkin.parsing.text_layer import _parse_text_line, _parse_text_line_all

# Склеенная строка из sample_013: плотность + pH на одной строке
merged = "Относительная плотность 1017 г/л 1003 - 1035 pH 5.5 5 - 8"
print(f"Исходная строка: {merged!r}")
print(f"\n_parse_text_line (один результат):")
r = _parse_text_line(merged)
if r:
    print(f"  name={r.analyte_name!r} val={r.value_raw} ref={r.ref_low}-{r.ref_high}")

print(f"\n_parse_text_line_all (все результаты):")
for r in _parse_text_line_all(merged):
    print(f"  name={r.analyte_name!r} val={r.value_raw} ref={r.ref_low}-{r.ref_high}")

# Обычная строка — не должна сломаться
normal = "Гемоглобин 12.6 г/дл 11.7 - 15.5"
print(f"\nОбычная строка: {normal!r}")
for r in _parse_text_line_all(normal):
    print(f"  name={r.analyte_name!r} val={r.value_raw} ref={r.ref_low}-{r.ref_high}")
