"""Соответствия единиц измерения → каноничная форма.

Ключи нормализованы (lower, без пробелов). Значения — каноничное отображение.
Данные загружаются из units.json.
"""

import json
from pathlib import Path

_UNIT_ALIASES_PATH = Path(__file__).parent / "units.json"
UNIT_ALIASES: dict[str, str] = json.loads(
    _UNIT_ALIASES_PATH.read_text(encoding="utf-8")
)
