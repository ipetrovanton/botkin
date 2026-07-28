"""Typed application settings using Pydantic models.

Replaces the module-level constants in config.py with structured, validated settings.
Priority: env vars > config.json > defaults.
"""

from botkin.settings.app import AppSettings, get_settings

__all__ = ["AppSettings", "get_settings"]
