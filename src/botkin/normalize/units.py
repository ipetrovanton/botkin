"""Канонизация единиц измерения лабораторных показателей."""
from __future__ import annotations

from botkin.reference.units import UNIT_ALIASES


def _key(raw: str) -> str:
    return raw.strip().lower().replace(" ", "")


def canonical_unit(raw: str | None) -> tuple[str | None, str | None]:
    """Возвращает (каноничная_единица | None, сырая | None).

    Неизвестные единицы возвращаются как есть (не теряем данные).
    """
    if raw is None:
        return (None, None)
    canon = UNIT_ALIASES.get(_key(raw), raw.strip())
    return (canon, raw)
