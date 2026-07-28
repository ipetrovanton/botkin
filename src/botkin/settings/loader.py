"""Loader that merges config.json and env vars into settings models.

Priority: env vars > config.json > model defaults.
"""

import json
import os
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent.parent
_config_path = _project_root / "config.json"


def _load_json_config() -> dict:
    if not _config_path.exists():
        return {}
    try:
        with open(_config_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _get_json_value(key_path: str) -> Any:
    """Resolve a dotted key path from config.json."""
    value: Any = _load_json_config()
    for part in key_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _as_bool(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def resolve(key_path: str, env_name: str, cast: type = str, default: Any = None) -> Any:
    """Resolve a setting: env var > config.json > default."""
    raw = os.getenv(env_name)
    if raw is not None:
        return cast(raw)
    json_val = _get_json_value(key_path)
    if json_val is not None:
        return cast(json_val)
    return default
