"""Отладка multi-result парсинга."""
from botkin.parsing.text_layer import _collapse_numeric_spaces, _extract_unit_ref, _VALUE_TOKEN_RE, _is_analyzer_token, _parse_text_line_all
from botkin.parsing.scalars import parse_lab_value, parse_reference_range
from botkin.domain.models import LabResult

merged = "Относительная плотность 1017 г/л 1003 - 1035 pH 5.5 5 - 8"
tokens = merged.split()
print(f"tokens: {tokens}")
print(f"len: {len(tokens)}")

# First iteration
paren_depth = 0
vi = None
for i, tok in enumerate(tokens):
    paren_depth += tok.count("(") - tok.count(")")
    if paren_depth == 0 and _VALUE_TOKEN_RE.match(tok):
        if not _is_analyzer_token(tokens[i - 1] if i > 0 else "", tok):
            vi = i
            break
print(f"vi={vi}, tokens[vi]={tokens[vi]!r}")

name = " ".join(tokens[:vi]).strip()
print(f"name={name!r}")

rest = tokens[vi + 1:]
print(f"rest={rest}, len={len(rest)}")

collapsed = _collapse_numeric_spaces(rest)
print(f"collapsed={collapsed}, len={len(collapsed)}")

unit, ref, consumed = _extract_unit_ref(collapsed)
print(f"unit={unit!r}, ref={ref!r}, consumed={consumed}")

end = vi + 1 + max(consumed, len(rest) - len(collapsed) + consumed)
print(f"end={end}, len(tokens)={len(tokens)}, end>=len: {end >= len(tokens)}")

if end < len(tokens):
    remaining = tokens[end:]
    print(f"remaining tokens: {remaining}")

    # Second iteration
    paren_depth = 0
    vi2 = None
    for i, tok in enumerate(remaining):
        paren_depth += tok.count("(") - tok.count(")")
        if paren_depth == 0 and _VALUE_TOKEN_RE.match(tok):
            if not _is_analyzer_token(remaining[i - 1] if i > 0 else "", tok):
                vi2 = i
                break
    print(f"vi2={vi2}")
    if vi2 is not None:
        print(f"name2={' '.join(remaining[:vi2])!r}")
        rest2 = remaining[vi2 + 1:]
        print(f"rest2={rest2}")
        collapsed2 = _collapse_numeric_spaces(rest2)
        unit2, ref2, consumed2 = _extract_unit_ref(collapsed2)
        print(f"unit2={unit2!r}, ref2={ref2!r}, consumed2={consumed2}")
