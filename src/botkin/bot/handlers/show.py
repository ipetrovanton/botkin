"""Команда /show /last — показать последний документ."""
import html
import json

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from botkin.bot.cards import format_card_header
from botkin.bot.keyboards import card_keyboard
from botkin.db.queries import (
    get_doctor_reports, get_lab_results, get_last_document, get_user_id,
)

router = Router(name="show")


@router.message(Command("show", "last"))
async def cmd_show(message: Message) -> None:
    user_id = get_user_id(message.from_user.id)
    if not user_id:
        await message.answer("⚠️ Отправь /start для регистрации.")
        return

    doc = get_last_document(user_id)
    if not doc:
        await message.answer("📭 Документов пока нет.")
        return

    doc_id = doc["id"]
    details = _format_document(doc_id, doc)
    await message.answer(
        f"{format_card_header(doc)}\n────────────\n{details}",
        reply_markup=card_keyboard(doc_id, has_prev=False, has_next=False),
    )


def _format_document(doc_id: int, doc: dict) -> str:
    doc_type = doc["doc_type"]
    if doc_type == "analysis":
        return _format_labs(get_lab_results(doc_id))
    elif doc_type == "doctor_report":
        return _format_doctor_reports(get_doctor_reports(doc_id))
    else:
        return (
            "ℹ️ Распознавание этого типа документа (например, рецептов) "
            "пока не поддерживается — сохранён только сам файл."
        )


def _format_ref(r: dict) -> str:
    """Текст нормы: двусторонняя / односторонняя с оператором / текстовая."""
    if r.get("ref_low") is not None and r.get("ref_high") is not None:
        return f"норма {r['ref_low']}–{r['ref_high']}"
    op = r.get("ref_operator")
    if op == "<" and r.get("ref_high") is not None:
        return f"норма <{r['ref_high']}"
    if op == ">" and r.get("ref_low") is not None:
        return f"норма >{r['ref_low']}"
    if r.get("ref_text"):
        return f"норма: {r['ref_text']}"
    return ""


def _ref_marker(r: dict) -> str:
    """⬆️/⬇️ по доступным границам (в т.ч. односторонним)."""
    v = r.get("value_num")
    if v is None:
        return ""
    low, high = r.get("ref_low"), r.get("ref_high")
    if low is not None and v < low:
        return " ⬇️"
    if high is not None and v > high:
        return " ⬆️"
    return ""


def _format_labs(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if r.get("value_num") is not None:
            value = f"{r['value_num']}"
        elif r.get("value_text"):
            value = html.escape(r["value_text"])
        else:
            continue
        name = html.escape(r.get("analyte_canonical") or r["analyte_name"])
        unit = f" {html.escape(r['unit'])}" if r.get("unit") else ""
        # _format_ref — текстовый helper; экранируем на границе HTML:
        # операторы «<»/«>» и свободный ref_text иначе ломают parse_mode=HTML.
        ref = _format_ref(r)
        ref = f" ({html.escape(ref)})" if ref else ""
        warn = " ⚠️" if r.get("unit_mismatch") else ""
        marker = _ref_marker(r)
        lines.append(f"• <b>{name}</b>: {value}{unit}{ref}{marker}{warn}")
    return "\n".join(lines) or "—"


def _format_doctor_reports(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        if r["diagnosis"]:
            lines.append(f"🔬 <b>Диагноз:</b> {html.escape(r['diagnosis'])}")
        if r["doctor_name"]:
            lines.append(f"👨‍⚕️ <b>Врач:</b> {html.escape(r['doctor_name'])}")
        if r["department"]:
            lines.append(f"🏥 <b>Отделение:</b> {html.escape(r['department'])}")
        if r["complaints_json"]:
            complaints = json.loads(r["complaints_json"])
            if complaints:
                lines.append(f"😷 <b>Жалобы:</b> {', '.join(html.escape(c) for c in complaints)}")
        if r["recommendations_json"]:
            recs = json.loads(r["recommendations_json"])
            if recs:
                lines.append("💡 <b>Рекомендации:</b>")
                for rec in recs:
                    lines.append(f"   • {html.escape(rec)}")
        if r["medications_json"]:
            meds = json.loads(r["medications_json"])
            if meds:
                lines.append("💊 <b>Назначения:</b>")
                for med in meds:
                    lines.append(f"   • {html.escape(med)}")
    return "\n".join(lines) or "-"