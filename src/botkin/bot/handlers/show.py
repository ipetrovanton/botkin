"""Команда /show /last — показать последний документ."""
import html
import json

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from botkin.bot.cards import format_card_header, format_rx_line
from botkin.db.queries import (
    get_doctor_reports, get_lab_results, get_last_document, get_prescriptions, get_user_id,
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
    await message.answer(f"{format_card_header(doc)}\n────────────\n{details}")


def _format_document(doc_id: int, doc: dict) -> str:
    doc_type = doc["doc_type"]
    if doc_type == "analysis":
        return _format_labs(get_lab_results(doc_id))
    elif doc_type == "prescription":
        return _format_rx(get_prescriptions(doc_id))
    elif doc_type == "doctor_report":
        return _format_doctor_reports(get_doctor_reports(doc_id))
    else:
        return html.escape(doc["raw_text"][:500]) if doc.get("raw_text") else ""


def _format_labs(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        marker = ""
        if r["value_num"] is not None and r["ref_low"] is not None and r["ref_high"] is not None:
            if r["value_num"] < r["ref_low"]:
                marker = " ⬇️"
            elif r["value_num"] > r["ref_high"]:
                marker = " ⬆️"
        ref = f" (норма {r['ref_low']}-{r['ref_high']})" if r["ref_low"] is not None else ""
        name = html.escape(r["analyte_name"])
        unit = html.escape(r["unit"]) if r["unit"] else ""
        lines.append(f"• <b>{name}</b>: {r['value_num']} {unit}{ref}{marker}")
    return "\n".join(lines) or "—"


def _format_rx(rows: list[dict]) -> str:
    return "\n".join(format_rx_line(r) for r in rows) or "-"


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