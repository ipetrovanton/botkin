"""Канонизация единиц измерения лабораторных показателей."""
from __future__ import annotations

import re
import unicodedata

from botkin.reference.units import UNIT_ALIASES

# Сворачиваем надстрочные Unicode-цифры («10⁹/л») в ASCII-нотацию «10^9/л».
# ¹²³ находятся в Latin-1 supplement (U+00B9/B2/B3), остальные — в U+2070-2079.
_SUPER_RE = re.compile(r"[\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+")


def _fold_superscripts(s: str) -> str:
    """Replace Unicode superscript digits with ASCII '^' notation."""
    def _replace(match: re.Match) -> str:
        result = []
        for ch in match.group():
            name = unicodedata.name(ch, "")
            if "SUPERSCRIPT" in name:
                # Extract the digit from the name (e.g., "SUPERSCRIPT ONE" → "1")
                digit_map = {
                    "SUPERSCRIPT ZERO": "0", "SUPERSCRIPT ONE": "1",
                    "SUPERSCRIPT TWO": "2", "SUPERSCRIPT THREE": "3",
                    "SUPERSCRIPT FOUR": "4", "SUPERSCRIPT FIVE": "5",
                    "SUPERSCRIPT SIX": "6", "SUPERSCRIPT SEVEN": "7",
                    "SUPERSCRIPT EIGHT": "8", "SUPERSCRIPT NINE": "9",
                }
                result.append(digit_map.get(name, ch))
            else:
                result.append(ch)
        return "^" + "".join(result)

    return _SUPER_RE.sub(_replace, s)


def _key(raw: str) -> str:
    return _fold_superscripts(raw).strip().lower().replace(" ", "")


def canonical_unit(raw: str | None) -> tuple[str | None, str | None]:
    """Возвращает (каноничная_единица | None, сырая | None).

    Неизвестные единицы возвращаются как есть (не теряем данные).
    """
    if raw is None:
        return (None, None)
    canon = UNIT_ALIASES.get(_key(raw), raw.strip())
    return (canon, raw)
