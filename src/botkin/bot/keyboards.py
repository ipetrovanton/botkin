"""Inline-клавиатуры и компактное кодирование callback_data (лимит 64 байта)."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

_SEP = ":"

# Коды типов для краткого callback_data.
TYPE_CODES = {"a": "analysis", "p": "prescription", "d": "doctor_report", "all": None}
CODE_BY_TYPE = {"analysis": "a", "prescription": "p", "doctor_report": "d", None: "all"}

PAGE_SIZE = 7
_FILTERS = [("🧪", "a"), ("💊", "p"), ("👨‍⚕️", "d"), ("Все", "all")]


def encode_cb(action: str, *parts) -> str:
    return _SEP.join([action, *[str(p) for p in parts]])


def decode_cb(data: str) -> tuple[str, list[str]]:
    action, *parts = data.split(_SEP)
    return action, parts


def list_keyboard(doc_ids: list[int], doc_type, offset: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    # ряд фильтров
    for label, code in _FILTERS:
        b.button(text=label, callback_data=encode_cb("lst", code, 0))
    # ряд номеров выбора
    for i, did in enumerate(doc_ids, start=1):
        b.button(text=str(i), callback_data=encode_cb("doc", did))
    # ряд пагинации
    code = CODE_BY_TYPE.get(doc_type, "all")
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(
            text="← Назад", callback_data=encode_cb("lst", code, max(0, offset - PAGE_SIZE))))
    if offset + PAGE_SIZE < total:
        nav_row.append(InlineKeyboardButton(
            text="Вперёд →", callback_data=encode_cb("lst", code, offset + PAGE_SIZE)))
    b.adjust(len(_FILTERS), len(doc_ids))
    kb = b.as_markup()
    if nav_row:
        kb.inline_keyboard.append(nav_row)
    return kb


def card_keyboard(doc_id: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    row = []
    if has_prev:
        row.append(InlineKeyboardButton(text="← Пред.", callback_data=encode_cb("nav", doc_id, "prev")))
    row.append(InlineKeyboardButton(text="☰ К списку", callback_data=encode_cb("lst", "all", 0)))
    if has_next:
        row.append(InlineKeyboardButton(text="След. →", callback_data=encode_cb("nav", doc_id, "next")))
    return InlineKeyboardMarkup(inline_keyboard=[row])
