"""Локальный справочник городов РФ с координатами для погодного контекста.

Данные: центры субъектов РФ + города с населением от 100 тыс. (перепись/оценка).
Координаты — WGS84 (dd.dd). Источник: открытые данные ФНС/Росстат (общественное достояние).
Формат: [{name, region, lat, lon, type}]. type: city/town/village.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "cities.json"

_CACHE: list[dict] | None = None


def _load() -> list[dict]:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return _CACHE


def search_cities(query: str, limit: int = 10) -> list[dict]:
    """Поиск городов по первым буквам. Регистронезависимый.

    Возвращает [{name, region, lat, lon, type, label}].
    label = "Город, Область" для удобства отображения.
    """
    q = query.strip().lower()
    if len(q) < 2:
        return []
    cities = _load()
    results: list[dict] = []
    for c in cities:
        if c["name"].lower().startswith(q):
            results.append({**c, "label": f"{c['name']}, {c['region']}"})
            if len(results) >= limit:
                break
    # Если мало — добавляем contains-поиск
    if len(results) < limit:
        for c in cities:
            if c not in results and q in c["name"].lower():
                results.append({**c, "label": f"{c['name']}, {c['region']}"})
                if len(results) >= limit:
                    break
    return results


def get_city_coordinates(name: str) -> tuple[float, float] | None:
    """Точные координаты по имени города (exact match, регистронезависимо)."""
    q = name.strip().lower()
    for c in _load():
        if c["name"].lower() == q:
            return c["lat"], c["lon"]
    return None
