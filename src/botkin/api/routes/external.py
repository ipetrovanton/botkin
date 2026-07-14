"""API: внешние данные — погода, геомагнитная активность, гороскоп (развлечение).

Отдаёт текущие внешние факторы для виджета в SPA и для RAG-контекста.
Все запросы к внешним API — graceful: при ошибке возвращаем null-поля.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from botkin.config import (
    EXT_ASTROLOGY_ENABLED, EXT_DEFAULT_LAT, EXT_DEFAULT_LON,
    EXT_GEOMAGNETIC_ENABLED, EXT_WEATHER_ENABLED,
)
from botkin.db.connection import get_conn
from botkin.db.repos import PatientRepo
from botkin.external import astrology, weather

from ..deps import get_user_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/external", tags=["external"])


@router.get("/today")
def today(user_id: int = Depends(get_user_id)) -> dict:
    """Текущие внешние данные: погода, Kp-индекс, гороскоп (если включён)."""
    lat, lon = EXT_DEFAULT_LAT, EXT_DEFAULT_LON
    birth_date = None
    with get_conn() as conn:
        profile = PatientRepo(conn, user_id).get_profile()
        if profile:
            if profile.get("latitude"):
                lat = profile["latitude"]
            if profile.get("longitude"):
                lon = profile["longitude"]
            birth_date = profile.get("birth_date")

    result: dict = {
        "weather": None,
        "geomagnetic": None,
        "horoscope": None,
    }

    if EXT_WEATHER_ENABLED:
        w = weather.fetch_weather(lat, lon)
        if w:
            result["weather"] = {
                "temperature": round(w.temperature, 1),
                "windspeed": round(w.windspeed, 1),
                "humidity": round(w.humidity, 1),
                "weather_code": w.weather_code,
                "description": _weather_desc(w.weather_code),
                "precipitation": round(w.precipitation, 2),
            }

    if EXT_GEOMAGNETIC_ENABLED:
        kp = weather.fetch_geomagnetic()
        if kp:
            result["geomagnetic"] = {
                "kp_index": round(kp.kp_index, 2),
                "level": kp.level,
            }

    if EXT_ASTROLOGY_ENABLED:
        sign = astrology.get_zodiac_sign(birth_date)
        horo = astrology.get_daily_horoscope(birth_date)
        if horo:
            result["horoscope"] = {"sign": sign, "text": horo}

    return result


def _weather_desc(code: int) -> str:
    return weather._WEATHER_CODE_MAP.get(code, "неизвестно")
