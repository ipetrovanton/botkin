"""FastAPI-приложение."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from botkin.db.connection import init_db
from botkin.api.routes import analytics, documents, health_sync, rag, upload
from botkin.llm.client import warmup
from botkin.log_config import setup_logging

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    # Прогрев моделей — фоном, чтобы не блокировать старт API (см. client.warmup).
    warmup_task = asyncio.create_task(asyncio.to_thread(warmup))
    yield
    warmup_task.cancel()


app = FastAPI(title="botkin API", version="0.2.0", lifespan=lifespan)

# API-роуты регистрируются ДО монтирования статики — Starlette проверяет маршруты
# по порядку, поэтому /api/*, /upload и /health перехватываются раньше catch-all '/'.
app.include_router(upload.router)
app.include_router(documents.router)
app.include_router(analytics.router)
app.include_router(health_sync.router)
app.include_router(rag.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# SPA веб-кабинета: html=True отдаёт index.html на '/'. Хеш-роутинг на клиенте —
# серверного fallback на произвольные пути не требуется.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
