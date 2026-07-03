"""FastAPI-приложение."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from botkin.db.connection import init_db
from botkin.api.routes import upload
from botkin.llm.client import warmup
from botkin.log_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    # Прогрев моделей — фоном, чтобы не блокировать старт API (см. client.warmup).
    warmup_task = asyncio.create_task(asyncio.to_thread(warmup))
    yield
    warmup_task.cancel()


app = FastAPI(title="botkin API", version="0.2.0", lifespan=lifespan)
app.include_router(upload.router)


@app.get("/health")
def health():
    return {"status": "ok"}