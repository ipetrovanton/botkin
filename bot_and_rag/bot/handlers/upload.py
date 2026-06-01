"""Принимаем фото/PDF/document от пользователя и шлём на backend API."""
import logging
from aiogram import Router, F
from aiogram.types import Message
import httpx

from backend.config import BOT_API_URL, UPLOAD_MAX_BYTES

router = Router(name="upload")
log = logging.getLogger("bot.upload")


async def _upload_to_api(tg_user_id: int, filename: str, file_bytes: bytes) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{BOT_API_URL}/upload",
            files={"file": (filename, file_bytes)},
            headers={"X-Telegram-User-Id": str(tg_user_id)},
        )
        resp.raise_for_status()
        return resp.json()


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    photo = message.photo[-1]
    file_info = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    filename = f"photo_{photo.file_unique_id}.jpg"

    await message.answer("📥 Принято, обрабатываю...")
    try:
        result = await _upload_to_api(
            message.from_user.id, filename, file_bytes.read()
        )
        doc_id = result["document_id"]
        await message.answer(
            f"✅ Документ #{doc_id} принят.\n"
            f"Статус: {result['status']}\nПодожди ~30-60 с, обрабатываю."
        )
    except httpx.HTTPStatusError as e:
        log.exception("Upload failed")
        await message.answer(f"❌ Ошибка загрузки: {e.response.status_code}")


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

    await message.answer("📥 Принято, обрабатываю...")
    try:
        result = await _upload_to_api(
            message.from_user.id, filename, file_bytes.read()
        )
        doc_id = result["document_id"]
        await message.answer(
            f"✅ Документ «{filename}» #{doc_id} принят.\n\nПодожди ~30-60 с."
        )
    except httpx.HTTPStatusError as e:
        log.exception("Upload failed")
        await message.answer(f"❌ Ошибка загрузки: {e.response.status_code}")