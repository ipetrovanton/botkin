"""Навигация по документам: /list, карточка, листание, фильтр."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from botkin.bot.cards import format_card_header, format_list_body
from botkin.bot.keyboards import (
    PAGE_SIZE, TYPE_CODES, card_keyboard, decode_cb, list_keyboard,
)
from botkin.db.queries import (
    count_documents, get_document, get_user_id, list_documents,
)

router = Router(name="browse")


async def _need_user(obj, tg_id: int) -> int | None:
    uid = get_user_id(tg_id)
    if not uid:
        answer = obj.answer if isinstance(obj, Message) else obj.message.answer
        await answer("⚠️ Отправь /start для регистрации.")
    return uid


def _render_card(doc_id: int, user_id: int):
    from botkin.bot.handlers.show import _format_document
    doc = get_document(doc_id, user_id)
    if not doc:
        return None, None
    # соседи по дате в пределах всех документов пользователя
    siblings = [d["id"] for d in list_documents(user_id, limit=10_000)]
    idx = siblings.index(doc_id) if doc_id in siblings else 0
    has_prev = idx < len(siblings) - 1     # список по убыванию даты → prev = старее
    has_next = idx > 0
    text = f"{format_card_header(doc)}\n────────────\n{_format_document(doc_id, doc)}"
    return text, card_keyboard(doc_id, has_prev=has_prev, has_next=has_next)


async def _show_list(target, user_id: int, code: str, offset: int):
    doc_type = TYPE_CODES.get(code)
    total = count_documents(user_id, doc_type=doc_type)
    docs = list_documents(user_id, doc_type=doc_type, limit=PAGE_SIZE, offset=offset)
    body = format_list_body(docs, offset=offset, total=total)
    kb = list_keyboard([d["id"] for d in docs], doc_type=doc_type, offset=offset, total=total)
    await target(body, reply_markup=kb)


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    uid = await _need_user(message, message.from_user.id)
    if not uid:
        return
    await _show_list(message.answer, uid, "all", 0)


@router.callback_query()
async def on_callback(cb: CallbackQuery) -> None:
    uid = await _need_user(cb, cb.from_user.id)
    if not uid:
        await cb.answer()
        return
    action, parts = decode_cb(cb.data)

    if action == "lst":
        code, offset = parts[0], int(parts[1])
        await _show_list(cb.message.edit_text, uid, code, offset)

    elif action == "doc":
        text, kb = _render_card(int(parts[0]), uid)
        if text is None:
            await cb.answer("Документ не найден", show_alert=True)
        else:
            await cb.message.edit_text(text, reply_markup=kb)

    elif action == "nav":
        doc_id, direction = int(parts[0]), parts[1]
        siblings = [d["id"] for d in list_documents(uid, limit=10_000)]
        if doc_id in siblings:
            i = siblings.index(doc_id)
            j = i + 1 if direction == "prev" else i - 1   # prev = старее (дальше по списку)
            if 0 <= j < len(siblings):
                text, kb = _render_card(siblings[j], uid)
                await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()
