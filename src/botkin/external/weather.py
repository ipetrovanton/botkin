"""Погода и геомагнитная активность из бесплатных API без ключей.

Источники:
- Open-Meteo (https://api.open-meteo.com) — погода без API-ключа, CC BY 4.0.
- NOAA SWPC (https://services.swpc.noaa.gov) — индекс Kp, публичный API US gov.

Все запросы — через стандартный urllib, без внешних зависимостей.
Таймаут 10с, graceful degradation: при ошибке возвращаем None, не роняем вызов.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_SWPC_KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
_TIMEOUT = 10


@dataclass
class WeatherData:
    temperature: float
    windspeed: float
    humidity: float
    weather_code: int
    precipitation: float

    def describe(self) -> str:
        """Краткое текстовое описание для RAG-контекста."""
        desc = _WEATHER_CODE_MAP.get(self.weather_code, "неизвестно")
        parts = [
            f"Погода: {desc}, температура {self.temperature:.0f}°C",
            f"ветер {self.windspeed:.0f} км/ч",
            f"влажность {self.humidity:.0f}%",
        ]
        if self.precipitation > 0:
            parts.append(f"осадки {self.precipitation:.1f} мм")
        return ", ".join(parts)


# WMO Weather interpretation codes (WW)
# https://open-meteo.com/en/docs (см. раздел "Weather variable documentation")
_WEATHER_CODE_MAP: dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "слабая морось",
    53: "морось",
    55: "сильная морось",
    56: "ледяная морось",
    57: "сильная ледяная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "сильный ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "ливень",
    81: "сильный ливень",
    82: "очень сильный ливень",
    85: "снежный ливень",
    86: "сильный снежный ливень",
    95: "гроза",
    96: "гроза с градом",
    99: "сильная гроза с градом",
}


@dataclass
class GeoMagneticData:
    kp_index: float
    level: str

    def describe(self) -> str:
        return f"Геомагнитная активность: Kp={self.kp_index:.1f} ({self.level})"


def _kp_level(kp: float) -> str:
    if kp < 2.0:
        return "спокойная"
    elif kp < 4.0:
        return "низкая"
    elif kp < 5.0:
        return "умеренная"
    elif kp < 6.0:
        return "высокая"
    elif kp < 8.0:
        return "очень высокая"
    return "экстремальная (магнитная буря)"


def fetch_weather(latitude: float, longitude: float) -> WeatherData | None:
    """Текущая погода через Open-Meteo. Возвращает None при ошибке сети."""
    params = (
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,relative_humidity_2m,weather_code,"
        "wind_speed_10m,precipitation"
    )
    url = _OPEN_METEO_URL + params
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
        cur = data["current"]
        return WeatherData(
            temperature=cur["temperature_2m"],
            windspeed=cur["wind_speed_10m"],
            humidity=cur["relative_humidity_2m"],
            weather_code=cur["weather_code"],
            precipitation=cur.get("precipitation", 0.0),
        )
    except Exception as e:
        log.warning("Не удалось получить погоду: %s", e)
        return None


def fetch_geomagnetic() -> GeoMagneticData | None:
    """Текущий индекс Kp от NOAA SWPC. Возвращает None при ошибке сети."""
    try:
        with urllib.request.urlopen(_SWPC_KP_URL, timeout=_TIMEOUT) as resp:
            rows = json.loads(resp.read())
        # Формат: [["time_tag", "Kp", ...], ["2024-01-01T00:00Z", "1.67", ...], ...]
        if not rows or len(rows) < 2:
            return None
        last = rows[-1]
        kp = float(last[1])
        return GeoMagneticData(kp_index=kp, level=_kp_level(kp))
    except Exception as e:
        log.warning("Не удалось получить Kp: %s", e)
        return None


def gather_external_context(latitude: float | None = None, longitude: float | None = None) -> str | None:
    """Объединённый блок внешних данных для RAG-контекста.

    Без координат погода пропускается — только геомагнитная активность.
    """
    parts: list[str] = []
    if latitude is not None and longitude is not None:
        w = fetch_weather(latitude, longitude)
        if w:
            parts.append(w.describe())
    kp = fetch_geomagnetic()
    if kp:
        parts.append(kp.describe())
    return "\n".join(parts) if parts else None
