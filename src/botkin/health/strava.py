"""Коннектор Strava (официальный API v3, OAuth2).

Требует зарегистрированного приложения (STRAVA_CLIENT_ID/SECRET в env) — без него
подключение отключено. Strava отдаёт только активности и их стримы; медицинских
метрик (давление, пульс покоя, сон) в API нет — тренировки дополняют картину
Garmin/Apple Health, но не заменяют их.

Лимиты API: 100 запросов / 15 мин, 1000 / день — синк активностей списком
укладывается в единицы запросов.
"""
from __future__ import annotations

import json
import logging
import urllib.parse

import httpx

from botkin.config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET

log = logging.getLogger(__name__)

PROVIDER = "strava"
_AUTH_URL = "https://www.strava.com/oauth/authorize"
_TOKEN_URL = "https://www.strava.com/oauth/token"
_API = "https://www.strava.com/api/v3"


def is_configured() -> bool:
    return bool(STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET)


def authorize_url(redirect_uri: str, state: str = "") -> str:
    """URL для редиректа пользователя на страницу согласия Strava."""
    params = {
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all",
        "state": state,
    }
    return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    """authorization code → токены. Возвращает JSON с access/refresh_token и athlete."""
    resp = httpx.post(_TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def refresh_tokens(refresh_token: str) -> dict:
    resp = httpx.post(_TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_activities(token_json: str, per_page: int = 100, pages: int = 3) -> list[dict]:
    """Активности за последние страницы списка → строки health_activities.

    Токены берутся из health_accounts.token_json; при истёкшем access —
    вызывающий код обновляет их через refresh_tokens и сохраняет обратно.
    """
    tokens = json.loads(token_json)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    rows: list[dict] = []
    with httpx.Client(timeout=30) as client:
        for page in range(1, pages + 1):
            resp = client.get(f"{_API}/athlete/activities", headers=headers,
                              params={"per_page": per_page, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for a in batch:
                rows.append({
                    "provider": PROVIDER,
                    "external_id": str(a["id"]),
                    "activity_type": a.get("sport_type") or a.get("type"),
                    "name": a.get("name"),
                    "started_at": (a.get("start_date_local") or "").replace("T", " ")[:19] or None,
                    "duration_s": a.get("moving_time"),
                    "distance_m": a.get("distance"),
                    "avg_hr": a.get("average_heartrate"),
                    "max_hr": a.get("max_heartrate"),
                    "calories": a.get("calories"),
                    "raw_json": json.dumps(a, ensure_ascii=False),
                })
    return rows
