"""Принимаем фото/PDF от пользователя и шлём на backend API."""
import asyncio
import logging
import time
from pathlib import Path

import httpx
from aiogram import F, Router
from aiogram.types import Message

from botkin.bot.document_view import compose_card
from botkin.bot.progress import poll_until_done, render_progress
from botkin.bot.services import (
    claim_delivery as _claim_delivery_svc,
    get_document as _get_document_svc,
    get_document_status as _get_status_svc,
    resolve_user as _resolve_user_svc,
)
from botkin.config import BOT_API_URL, BOT_PROGRESS_TIMEOUT, PHOTO_LOWRES_WARN, UPLOAD_MAX_BYTES

router = Router(name="upload")
log = logging.getLogger("botkin.bot.upload")

# Один httpx-клиент на процесс бота: пул соединений переиспользуется между аплоадами
# вместо пересоздания TCP/TLS-хендшейка на каждый вызов _upload_to_api.
_http_client = httpx.AsyncClient(timeout=60.0)

# Telegram сжимает «фото» (~1280px). Файлом сохраняется полное разрешение камеры.
_FILE_HINT = (
    "📎 Совет: для лучшего распознавания пришлите документ файлом "
    "(скрепка → Файл), а не фото — так сохранится полное разрешение."
)

# Telegram отдаёт mime для документов без расширения в имени.
_MIME_EXT = {
    "image/heic": ".heic", "image/heif": ".heif", "image/jpeg": ".jpg",
    "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf",
}


def photo_followup_text(image_long_side: int) -> str:
    """Подсказка после приёма фото; при низком разрешении — усиленное предупреждение."""
    if image_long_side < PHOTO_LOWRES_WARN:
        return (
            "⚠️ Фото пришло в низком разрешении — качество распознавания может пострадать.\n"
            + _FILE_HINT
        )
    return _FILE_HINT


async def close_http_client() -> None:
    """Закрыть общий httpx-клиент при остановке бота (вызывается из bot/main.py)."""
    await _http_client.aclose()


async def _upload_to_api(tg_user_id: int, filename: str, file_bytes: bytes) -> dict:
    resp = await _http_client.post(
        f"{BOT_API_URL}/upload",
        files={"file": (filename, file_bytes)},
        headers={"X-Telegram-User-Id": str(tg_user_id)},
    )
    resp.raise_for_status()
    return resp.json()


def _resolve_user_id(tg_user_id: int) -> int | None:
    """user_id по telegram-id (реэкспорт service для тестов)."""
    return _resolve_user_svc(tg_user_id)


def render_document_card(doc_id: int, user_id: int) -> str:
    """Полная карточка документа по id (реэкспорт service для тестов)."""
    doc = _get_document_svc(doc_id, user_id)
    if not doc:
        return "❌ Документ не найден."
    return compose_card(doc_id, doc)


def claim_delivery_for(doc_id: int, user_id: int) -> bool:
    """Реэкспорт service для тестов."""
    return _claim_delivery_svc(doc_id, user_id)


async def run_progress_flow(tg_user_id: int, doc_id: int, edit) -> None:
    """Поллит статус и по завершении показывает карточку. `edit(text)` — корутина."""
    log.info("[FLOW_START] Doc %d | tg_user=%d", doc_id, tg_user_id)
    try:
        user_id = _resolve_user_id(tg_user_id)
        if not user_id:
            log.warning("[FLOW_NO_USER] Doc %d | tg_user=%d не зарегистрирован", doc_id, tg_user_id)
            return

        async def _get_status():
            return _get_status_svc(doc_id, user_id)

        final = await poll_until_done(
            doc_id=doc_id, get_status=_get_status, edit=edit,
            sleep=asyncio.sleep, now=time.monotonic, timeout=BOT_PROGRESS_TIMEOUT,
        )
        log.info("[FLOW_FINAL] Doc %d | final=%r", doc_id, final)
        if final == "extracted":
            claimed = claim_delivery_for(doc_id, user_id)
            log.info("[FLOW_CLAIM] Doc %d | claimed=%s", doc_id, claimed)
            if claimed:
                await edit(render_document_card(doc_id, user_id))
                log.info("[FLOW_CARD] Doc %d | карточка показана", doc_id)
            else:
                # Гонку забрал push-fallback pipeline: прогресс-бар иначе застынет на «Нормализую».
                log.warning("[FLOW_LOST_RACE] Doc %d | доставку забрал pipeline-fallback", doc_id)
        elif final == "failed":
            await edit(f"❌ Документ #{doc_id}: обработка завершилась ошибкой.")
        else:
            await edit("⏳ Обработка затянулась. Загляните позже через /show.")
    except Exception:
        log.exception("[FLOW_ERROR] Doc %d | сбой в run_progress_flow", doc_id)


async def _start_upload_flow(
    message: Message, filename: str, file_bytes: bytes, *, followup: str | None = None,
) -> None:
    """Грузит файл на API и запускает фоновый поллинг прогресса. Общий хвост on_photo/on_document.

    followup (подсказка про разрешение для фото) шлём ДО прогресс-сообщения — иначе фоновый
    таск успевает продвинуть прогресс-бар раньше подсказки.
    """
    try:
        result = await _upload_to_api(message.from_user.id, filename, file_bytes)
        doc_id = result["document_id"]
        if followup:
            await message.answer(followup)
        sent = await message.answer(render_progress("received", doc_id))

        async def _edit(text: str):
            try:
                await sent.edit_text(text)
            except Exception as e:  # noqa: BLE001 — "message is not modified" и пр.
                if "message is not modified" in str(e).lower():
                    log.debug("edit skipped (not modified): %s", e)
                else:
                    log.warning("[EDIT_FAIL] Doc %d | %d симв. | %s", doc_id, len(text), e)

        asyncio.create_task(run_progress_flow(message.from_user.id, doc_id, _edit))
    except httpx.HTTPStatusError as e:
        log.exception("Upload failed")
        await message.answer(f"❌ Ошибка загрузки: {e.response.status_code}")


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    photo = message.photo[-1]
    file_info = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    filename = f"photo_{photo.file_unique_id}.jpg"
    await _start_upload_flow(
        message, filename, file_bytes.read(), followup=photo_followup_text(photo.width),
    )


@router.message(F.document)
async def on_document(message: Message) -> None:
    doc = message.document
    if doc.file_size and doc.file_size > UPLOAD_MAX_BYTES:
        await message.answer(
            f"❌ Файл слишком большой ({doc.file_size // 1024 // 1024} MB), "
            f"макс. {UPLOAD_MAX_BYTES // 1024 // 1024} MB"
        )
        return

    file_info = await message.bot.get_file(doc.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    filename = doc.file_name or f"doc_{doc.file_unique_id}"
    if not Path(filename).suffix and doc.mime_type in _MIME_EXT:
        filename += _MIME_EXT[doc.mime_type]
    await _start_upload_flow(message, filename, file_bytes.read())