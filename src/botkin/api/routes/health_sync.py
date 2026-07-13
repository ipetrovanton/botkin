"""API веб-кабинета: подключение источников здоровья, синхронизация данных
и планировщик автосинка по расписанию пользователя."""
from __future__ import annotations

import asyncio
import json
import logging
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from botkin.config import HEALTH_SYNC_DAYS
from botkin.db.connection import get_conn
from botkin.db.repos import HealthRepo
from botkin.health import apple, garmin, strava

from ..deps import get_user_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])

# Прогресс фоновых синков: user_id → {"state", "done", "total", "error"}.
# In-memory осознанно: один процесс API, прогресс не переживает рестарт (как и sync-таска).
_sync_progress: dict[int, dict] = {}
_sync_lock = threading.Lock()


class GarminConnectRequest(BaseModel):
    email: str
    password: str


@router.get("/accounts")
def accounts(user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        items = HealthRepo(conn, user_id).list_accounts()
    return {"items": items, "strava_configured": strava.is_configured()}


@router.post("/connect/garmin")
def connect_garmin(
    req: GarminConnectRequest, user_id: int = Depends(get_user_id),
) -> dict:
    """Логин по паролю один раз; дальше живём на токенах. Пароль не сохраняется."""
    try:
        info = garmin.connect(user_id, req.email, req.password)
    except Exception as e:
        log.warning("Garmin connect не удался для user %s: %s", user_id, e)
        raise HTTPException(status_code=502, detail=f"Не удалось войти в Garmin: {e}")
    with get_conn() as conn:
        HealthRepo(conn, user_id).upsert_account(
            "garmin", identifier=info["identifier"], token_path=info["token_path"],
        )
    return {"status": "connected", "full_name": info.get("full_name")}


@router.delete("/accounts/{provider}")
def disconnect(provider: str, user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        HealthRepo(conn, user_id).disconnect(provider)
    return {"status": "disconnected"}


def _run_garmin_sync(user_id: int, days: int) -> None:
    def on_progress(done: int, total: int) -> None:
        with _sync_lock:
            _sync_progress[user_id] = {"state": "running", "done": done, "total": total}

    try:
        metrics, activities = garmin.fetch(user_id, days, on_progress=on_progress)
        with get_conn() as conn:
            repo = HealthRepo(conn, user_id)
            repo.save_metrics(metrics)
            repo.save_activities(activities)
            repo.mark_synced("garmin")
        # Обновляем RAG-сводки по свежим данным; сбой индексации не должен ронять синк.
        try:
            from botkin.rag.indexer import index_health
            index_health(user_id)
        except Exception as e:
            log.warning("RAG-индексация health для user %s не удалась: %s", user_id, e)
        with _sync_lock:
            _sync_progress[user_id] = {
                "state": "done", "metrics": len(metrics), "activities": len(activities),
            }
        log.info("Garmin sync user %s: %d метрик, %d активностей",
                 user_id, len(metrics), len(activities))
    except Exception as e:
        log.exception("Garmin sync user %s упал", user_id)
        with get_conn() as conn:
            HealthRepo(conn, user_id).mark_error("garmin", str(e))
        with _sync_lock:
            _sync_progress[user_id] = {"state": "error", "error": str(e)[:300]}


@router.post("/sync/garmin")
def sync_garmin(
    background_tasks: BackgroundTasks,
    days: int = Query(HEALTH_SYNC_DAYS, ge=1, le=365),
    user_id: int = Depends(get_user_id),
) -> dict:
    with get_conn() as conn:
        account = HealthRepo(conn, user_id).get_account("garmin")
    if not account or account["status"] == "disconnected":
        raise HTTPException(status_code=409, detail="Garmin не подключён")
    with _sync_lock:
        if _sync_progress.get(user_id, {}).get("state") == "running":
            raise HTTPException(status_code=409, detail="Синхронизация уже идёт")
        _sync_progress[user_id] = {"state": "running", "done": 0, "total": days + 2}
    background_tasks.add_task(_run_garmin_sync, user_id, days)
    return {"status": "started", "days": days}


class ScheduleRequest(BaseModel):
    """Частота автосинка в часах; null = только вручную. Минимум 1ч — чаще нет смысла:
    Garmin отдаёт дневные агрегаты, а частый опрос упирается в их rate limit."""

    interval_hours: int | None = None


@router.patch("/accounts/{provider}/schedule")
def set_schedule(
    provider: str, req: ScheduleRequest, user_id: int = Depends(get_user_id),
) -> dict:
    if req.interval_hours is not None and not (1 <= req.interval_hours <= 168):
        raise HTTPException(status_code=422, detail="Интервал: от 1 до 168 часов")
    with get_conn() as conn:
        if not HealthRepo(conn, user_id).set_sync_interval(provider, req.interval_hours):
            raise HTTPException(status_code=404, detail="Источник не подключён")
    return {"provider": provider, "interval_hours": req.interval_hours}


# Шаг проверки расписания. 15 мин достаточно: минимальный интервал автосинка — 1 час.
SCHEDULER_TICK_SECONDS = 900


async def scheduler_loop() -> None:
    """Фоновый автосинк: запускается из lifespan API-процесса.

    Отдельного планировщика (celery/cron) не заводим: один процесс API,
    asyncio-цикла достаточно; пропущенный за время дауна тик наверстается
    при следующем старте (сравнение идёт по last_sync_at, а не по таймеру)."""
    while True:
        try:
            await run_due_syncs()
        except Exception:
            log.exception("Планировщик автосинка: сбой тика")
        await asyncio.sleep(SCHEDULER_TICK_SECONDS)


async def run_due_syncs() -> None:
    """Один тик планировщика: синк всех аккаунтов с истёкшим интервалом."""
    with get_conn() as conn:
        due = HealthRepo.accounts_due_for_sync(conn)
    for account in due:
        if account["provider"] != "garmin":
            continue  # автосинк пока только для Garmin (strava/apple — push-модель)
        uid = account["user_id"]
        with _sync_lock:
            if _sync_progress.get(uid, {}).get("state") == "running":
                continue  # ручной синк уже идёт — не дублируем
            _sync_progress[uid] = {"state": "running", "done": 0, "total": HEALTH_SYNC_DAYS + 2}
        log.info("Автосинк Garmin по расписанию: user %s", uid)
        # fetch — блокирующий (requests внутри garminconnect) → в поток
        await asyncio.to_thread(_run_garmin_sync, uid, HEALTH_SYNC_DAYS)


@router.get("/sync/status")
def sync_status(user_id: int = Depends(get_user_id)) -> dict:
    with _sync_lock:
        return _sync_progress.get(user_id) or {"state": "idle"}


@router.get("/metrics")
def metrics(user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        repo = HealthRepo(conn, user_id)
        return {"items": repo.distinct_metrics(), "stats": repo.stats()}


@router.get("/series")
def series(
    metric: str = Query(..., min_length=1),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    user_id: int = Depends(get_user_id),
) -> dict:
    with get_conn() as conn:
        points = HealthRepo(conn, user_id).metrics_series(
            metric, date_from=date_from, date_to=date_to, limit=limit,
        )
    if not points:
        raise HTTPException(status_code=404, detail=f"Нет данных по метрике «{metric}»")
    return {"metric": metric, "unit": points[-1].get("unit"), "points": points}


@router.get("/activities")
def activities(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_user_id),
) -> dict:
    with get_conn() as conn:
        items = HealthRepo(conn, user_id).list_activities(limit=limit, offset=offset)
    return {"items": items}


@router.post("/apple/import")
async def apple_import(
    file: UploadFile = File(...), user_id: int = Depends(get_user_id),
) -> dict:
    """Импорт export.zip из приложения «Здоровье» (ручной экспорт с iPhone)."""
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        rows = apple.parse_export_zip(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Не удалось разобрать экспорт: {e}")
    with get_conn() as conn:
        repo = HealthRepo(conn, user_id)
        saved = repo.save_metrics(rows)
        repo.upsert_account("apple_health", identifier="export.zip")
        repo.mark_synced("apple_health")
    return {"status": "imported", "metrics": saved}


@router.post("/apple/ingest")
async def apple_ingest(payload: dict, user_id: int = Depends(get_user_id)) -> dict:
    """Приём JSON от Health Auto Export (Automations → REST API → этот URL)."""
    rows = apple.parse_hae_payload(payload)
    if not rows:
        return {"status": "ok", "metrics": 0}
    with get_conn() as conn:
        repo = HealthRepo(conn, user_id)
        saved = repo.save_metrics(rows)
        repo.upsert_account("apple_health", identifier="health-auto-export")
        repo.mark_synced("apple_health")
    return {"status": "ok", "metrics": saved}


@router.get("/strava/authorize")
def strava_authorize(
    redirect_uri: str = Query(...), user_id: int = Depends(get_user_id),
) -> dict:
    if not strava.is_configured():
        raise HTTPException(
            status_code=501,
            detail="Strava не сконфигурирована: задайте STRAVA_CLIENT_ID/SECRET в .env",
        )
    return {"url": strava.authorize_url(redirect_uri, state=str(user_id))}


@router.post("/strava/exchange")
def strava_exchange(code: str = Query(...), user_id: int = Depends(get_user_id)) -> dict:
    """Обмен authorization code на токены после редиректа со Strava."""
    if not strava.is_configured():
        raise HTTPException(status_code=501, detail="Strava не сконфигурирована")
    try:
        tokens = strava.exchange_code(code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Strava OAuth не удался: {e}")
    athlete = tokens.get("athlete") or {}
    with get_conn() as conn:
        HealthRepo(conn, user_id).upsert_account(
            "strava",
            identifier=str(athlete.get("id") or athlete.get("username") or ""),
            token_json=json.dumps(tokens),
        )
    return {"status": "connected"}


@router.post("/sync/strava")
def sync_strava(user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        repo = HealthRepo(conn, user_id)
        account = repo.get_account("strava")
        if not account or not account.get("token_json"):
            raise HTTPException(status_code=409, detail="Strava не подключена")
        token_json = account["token_json"]
        try:
            tokens = json.loads(token_json)
            refreshed = strava.refresh_tokens(tokens["refresh_token"])
            token_json = json.dumps(refreshed)
            repo.upsert_account("strava", identifier=account.get("identifier"),
                                token_json=token_json)
            rows = strava.fetch_activities(token_json)
            saved = repo.save_activities(rows)
            repo.mark_synced("strava")
        except Exception as e:
            repo.mark_error("strava", str(e))
            raise HTTPException(status_code=502, detail=f"Синк Strava не удался: {e}")
    return {"status": "done", "activities": saved}
