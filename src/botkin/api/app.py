"""FastAPI-приложение."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from botkin.db.connection import init_db
from botkin.api.routes import upload
from botkin.log_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    yield


app = FastAPI(title="botkin API", version="0.2.0", lifespan=lifespan)
app.include_router(upload.router)


@app.get("/health")
def health():
    return {"status": "ok"}