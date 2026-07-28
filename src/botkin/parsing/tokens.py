"""Shared token and numeric utilities — single source of truth.

Replaces 4 duplicated copies of _to_float across scalars.py, androflor.py,
sibr.py, and clinical/facts.py.
"""

from __future__ import annotations


def to_float(value: str) -> float:
    """Convert a numeric string to float, replacing comma with dot.

    Used by scalars, androflor, sibr, and clinical facts parsers.
    """
    return float(str(value).replace(",", "."))


def as_float(value: object) -> float | None:
    """Safe float conversion from arbitrary value.

    Returns None for None, bool, or non-convertible values.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
