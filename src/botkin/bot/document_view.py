"""Рендер деталей документа в текст карточки — чистый слой без роутеров.

Живёт отдельно от хендлеров (show/upload/browse), чтобы все трое импортировали рендер
сверху, а не отложенным `from ...show import _format_document` внутри функций (разрыв
латентного цикла импорта). Здесь же общая композиция карточки `compose_card`, которая
раньше копипастилась в трёх местах.
"""
import html
import json

from botkin.bot.cards import format_card_header, ref_marker
from botkin.db.connection import get_conn
from botkin.db.repos import LabRepo, ReportRepo


def _format_document(doc_id: int, doc: dict) -> str:
    doc_type = doc["doc_type"]
    user_id = doc["user_id"]
    if doc_type == "analysis":
        with get_conn() as conn:
            rows = LabRepo(conn, user_id).for_document(doc_id)
        return _format_labs(rows)
    elif doc_type == "doctor_report":
        with get_conn() as conn:
            rows = ReportRepo(conn, user_id).for_document(doc_id)
        return _format_doctor_reports(rows)
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
        marker = ref_marker(r.get("value_num"), r.get("ref_low"), r.get("ref_high"))
        lines.append(f"{len(lines) + 1}. <b>{name}</b>: {value}{unit}{ref}{marker}{warn}")
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


def compose_card(doc_id: int, doc: dict) -> str:
    """Шапка + разделитель + детали — единая композиция текста карточки для всех хендлеров."""
    return f"{format_card_header(doc)}\n────────────\n{_format_document(doc_id, doc)}"
