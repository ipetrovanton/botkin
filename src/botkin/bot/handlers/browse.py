"""Навигация по документам: /list, /period, карточка, листание, фильтр."""
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from botkin.bot.cards import format_labs_summary, format_list_body
from botkin.bot.document_view import compose_card
from botkin.bot.keyboards import (
    PAGE_SIZE, TYPE_CODES, card_keyboard, decode_cb, list_keyboard,
    period_presets_keyboard, period_view_keyboard,
)
from botkin.bot.period import parse_manual, preset_range
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, UserRepo

router = Router(name="browse")


async def _need_user(obj, tg_id: int) -> int | None:
    with get_conn() as conn:
        uid = UserRepo(conn).get_id(tg_id)
    if not uid:
        answer = obj.answer if isinstance(obj, Message) else obj.message.answer
        await answer("⚠️ Отправь /start для регистрации.")
    return uid


def _render_card(doc_id: int, user_id: int):
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        doc = repo.get(doc_id)
        if not doc:
            return None, None
        # соседи по дате — прямым индексным запросом, без выгрузки всего списка id
        has_prev = repo.adjacent_id(doc_id, older=True) is not None
        has_next = repo.adjacent_id(doc_id, older=False) is not None
    text = compose_card(doc_id, doc)
    return text, card_keyboard(doc_id, has_prev=has_prev, has_next=has_next)


async def _show_list(target, user_id: int, code: str, offset: int):
    doc_type = TYPE_CODES.get(code)
    with get_conn() as conn:
        repo = DocumentRepo(conn, user_id)
        total = repo.count(doc_type=doc_type)
        docs = repo.list(doc_type=doc_type, limit=PAGE_SIZE, offset=offset)
    body = format_list_body(docs, offset=offset, total=total)
    kb = list_keyboard([d["id"] for d in docs], doc_type=doc_type, offset=offset, total=total)
    await target(body, reply_markup=kb)


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    uid = await _need_user(message, message.from_user.id)
    if not uid:
        return
    await _show_list(message.answer, uid, "all", 0)


async def _period_labs(target, user_id, start, end, label):
    with get_conn() as conn:
        groups = LabRepo(conn, user_id).in_period(start, end)
    await target(format_labs_summary(groups, label=label))


async def _show_period_docs(target, user_id, start, end, preset):
    with get_conn() as conn:
        docs = DocumentRepo(conn, user_id).in_period(start, end, limit=PAGE_SIZE, offset=0)
    body = format_list_body(docs, offset=0, total=len(docs))
    kb = list_keyboard([d["id"] for d in docs], doc_type=None, offset=0, total=len(docs))
    await target(body, reply_markup=kb)


@router.message(Command("period"))
async def cmd_period(message: Message, command: CommandObject) -> None:
    uid = await _need_user(message, message.from_user.id)
    if not uid:
        return
    args = (command.args or "").split()
    if args:
        rng = parse_manual(args)
        if not rng:
            await message.answer(
                "Формат: /period 2026-01 2026-03  или  /period 2026-01-01 2026-01-31")
            return
        start, end = rng
        await _period_labs(message.answer, uid, start, end, label=f"{args[0]}–{args[1]}")
        return
    await message.answer("📅 За какой период?", reply_markup=period_presets_keyboard())


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
        with get_conn() as conn:
            target_id = DocumentRepo(conn, uid).adjacent_id(doc_id, older=(direction == "prev"))
        if target_id is not None:
            text, kb = _render_card(target_id, uid)
            await cb.message.edit_text(text, reply_markup=kb)

    elif action == "per":
        preset, view = parts[0], parts[1]
        if view == "menu":
            await cb.message.edit_text(f"📅 {preset} — что показать?",
                                       reply_markup=period_view_keyboard(preset))
        else:
            start, end = preset_range(preset, now=datetime.now())
            if view == "docs":
                await _show_period_docs(cb.message.edit_text, uid, start, end, preset)
            else:
                await _period_labs(cb.message.edit_text, uid, start, end, label=preset)
    await cb.answer()
