"""Канонизация единиц измерения лабораторных показателей."""
from __future__ import annotations

import re

from botkin.reference.units import UNIT_ALIASES

# Надстрочные Unicode-цифры степени («10⁹/л») сворачиваем в ASCII-нотацию «10^9/л»,
# чтобы и форма из текстового слоя PDF, и форма из реестра ФСЛИ совпадали по ключу.
_SUPERSCRIPT = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}
_SUPER_RE = re.compile("[" + "".join(_SUPERSCRIPT) + "]+")


def _fold_superscripts(s: str) -> str:
    return _SUPER_RE.sub(lambda m: "^" + "".join(_SUPERSCRIPT[c] for c in m.group()), s)


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
