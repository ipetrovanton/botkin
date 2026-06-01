"""Команда /show /last — показать последний документ."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from backend.db.connection import get_conn

router = Router(name="show")


@router.message(Command("show", "last"))
async def cmd_show(message: Message) -> None:
    tg_user_id = message.from_user.id
    with get_conn() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?", (tg_user_id,)
        ).fetchone()
        if not user:
            await message.answer("⚠️ Отправь /start для регистрации.")
            return
        user_id = user["id"]

        doc = conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not doc:
            await message.answer("📭 Документов пока нет.")
            return
        doc_id = doc["id"]

        if doc["doc_type"] == "analysis":
            labs = conn.execute(
                "SELECT analyte_name, value_num, unit, ref_low, ref_high "
                "FROM lab_results WHERE document_id = ? LIMIT 20",
                (doc_id,),
            ).fetchall()
            details = _format_labs(labs)
        elif doc["doc_type"] == "prescription":
            rx = conn.execute(
                "SELECT drug_mnn, drug_trade, dose, frequency, duration_days "
                "FROM prescriptions WHERE document_id = ?",
                (doc_id,),
            ).fetchall()
            details = _format_rx(rx)
        elif doc["doc_type"] == "doctor_report":
            reports = conn.execute(
                "SELECT diagnosis, recommendations_json, complaints_json, "
                "medications_json, doctor_name, department "
                "FROM doctor_reports WHERE document_id = ?",
                (doc_id,),
            ).fetchall()
            details = _format_doctor_reports(reports)
        else:
            import html
            details = html.escape(doc["raw_text"][:500]) if doc["raw_text"] else ""

    status_emoji = {
        "received": "📥", "processing": "⏳", "extracted": "✅", "failed": "❌",
    }.get(doc["status"], "❓")
    type_emoji = {
        "analysis": "🧪", "prescription": "💊", "doctor_report": "👨‍⚕️",
    }.get(doc["doc_type"], "📄")

    await message.answer(
        f"{status_emoji} Документ #{doc['id']}  {type_emoji}\n"
        f"Тип: {doc['doc_type']}\n"
        f"Загружен: {doc['created_at']}\n\n{details}"
    )


def _format_labs(rows) -> str:
    import html
    lines = []
    for r in rows:
        marker = ""
        if (
            r["value_num"] is not None
            and r["ref_low"] is not None
            and r["ref_high"] is not None
        ):
            if r["value_num"] < r["ref_low"]:
                marker = " ⬇️"
            elif r["value_num"] > r["ref_high"]:
                marker = " ⬆️"
        ref = (
            f" (норма {r['ref_low']}-{r['ref_high']})"
            if r["ref_low"] is not None
            else ""
        )
        analyte_name = html.escape(r["analyte_name"])
        unit = html.escape(r["unit"]) if r["unit"] else ""
        lines.append(f"• <b>{analyte_name}</b>: {r['value_num']} {unit}{ref}{marker}")
    return "\n".join(lines) or "—"


def _format_rx(rows) -> str:
    import html
    lines = []
    for r in rows:
        trade = f" ({html.escape(r['drug_trade'])})" if r["drug_trade"] else ""
        dur = f", {r['duration_days']} дн." if r["duration_days"] else ""
        drug_mnn = html.escape(r["drug_mnn"])
        dose = html.escape(r["dose"]) if r["dose"] else ""
        frequency = html.escape(r["frequency"]) if r["frequency"] else ""
        lines.append(f"• <b>{drug_mnn}{trade}</b>: {dose}, {frequency}{dur}")
    return "\n".join(lines) or "-"


def _format_doctor_reports(rows) -> str:
    import json
    import html
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
                escaped = [html.escape(c) for c in complaints]
                lines.append(f"😷 <b>Жалобы:</b> {', '.join(escaped)}")
        if r["recommendations_json"]:
            recommendations = json.loads(r["recommendations_json"])
            if recommendations:
                lines.append("💡 <b>Рекомендации:</b>")
                for rec in recommendations:
                    lines.append(f"   • {html.escape(rec)}")
        if r["medications_json"]:
            medications = json.loads(r["medications_json"])
            if medications:
                lines.append("💊 <b>Назначения:</b>")
                for med in medications:
                    lines.append(f"   • {html.escape(med)}")
    return "\n".join(lines) or "-"