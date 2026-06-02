"""Рендер карточек документов для Telegram (чистые функции, тестируемы без БД)."""
import html
import json

from botkin.domain.models import DOC_TYPE_LABELS

_PROBLEM_STATUSES = {"expired", "excluded", "suspended"}

STATUS_EMOJI = {"received": "📥", "recognizing": "🔍", "normalizing": "🧩",
                "extracted": "✅", "failed": "❌"}
TYPE_EMOJI = {"analysis": "🧪", "prescription": "💊", "doctor_report": "👨‍⚕️",
              "certificate": "📄", "unknown": "📄"}


def doc_title(doc: dict) -> str:
    """Название документа: title, иначе лейбл типа."""
    if doc.get("title"):
        return html.escape(doc["title"])
    return DOC_TYPE_LABELS.get(doc.get("doc_type", "unknown"), "Документ 📄")


def format_card_header(doc: dict) -> str:
    status = STATUS_EMOJI.get(doc.get("status"), "❓")
    type_e = TYPE_EMOJI.get(doc.get("doc_type"), "📄")
    clinic = html.escape(doc["clinic"]) if doc.get("clinic") else "—"
    return (
        f"{status} Документ #{doc['id']} · {type_e} {doc_title(doc)}\n"
        f"🏥 {clinic} · {doc.get('created_at', '')}"
    )


def _reg_warning(reg_statuses_json: str | None) -> str:
    """⚠️ если в наборе статусов ГРЛС нет ни одного 'active'."""
    if not reg_statuses_json:
        return ""
    try:
        statuses = set(json.loads(reg_statuses_json))
    except (ValueError, TypeError):
        return ""
    if "active" in statuses:
        return ""
    if statuses & _PROBLEM_STATUSES:
        return "  ⚠️ нет действующих регистраций в РФ"
    return ""


def format_rx_line(r: dict) -> str:
    """Одна строка назначения с пометками ГРЛС."""
    mnn = html.escape(r["drug_mnn"])
    trade = f" ({html.escape(r['drug_trade'])})" if r.get("drug_trade") else ""
    dose = html.escape(r["dose"]) if r.get("dose") else ""
    freq = html.escape(r["frequency"]) if r.get("frequency") else ""
    dur = f", {r['duration_days']} дн." if r.get("duration_days") else ""

    flags = _reg_warning(r.get("reg_statuses"))
    if r.get("match_status") == "unverified":
        ratio = r.get("ratio")
        pct = f" ({round(ratio * 100)}%)" if isinstance(ratio, (int, float)) else ""
        flags += f"  ❓ распознано неточно{pct} — проверьте"

    parts = ", ".join(p for p in [dose, freq] if p)
    return f"• <b>{mnn}{trade}</b>: {parts}{dur}{flags}"
