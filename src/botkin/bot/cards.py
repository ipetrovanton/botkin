"""Рендер карточек документов для Telegram (чистые функции, тестируемы без БД)."""
import html

from botkin.domain.models import DOC_TYPE_LABELS

STATUS_EMOJI = {"received": "📥", "recognizing": "🔍", "normalizing": "🧩",
                "extracted": "✅", "failed": "❌"}
TYPE_EMOJI = {"analysis": "🧪", "doctor_report": "👨‍⚕️",
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


def format_list_body(docs: list[dict], offset: int, total: int) -> str:
    if not docs:
        return "📭 Документов пока нет."
    head = f"📁 Твои документы ({offset + 1}–{offset + len(docs)} из {total})\n"
    lines = [head]
    for i, d in enumerate(docs, start=1):
        type_e = TYPE_EMOJI.get(d.get("doc_type"), "📄")
        clinic = html.escape(d["clinic"]) if d.get("clinic") else "—"
        date = str(d.get("created_at", ""))[:10]
        lines.append(f"{i}. {type_e} {doc_title(d)}\n   🏥 {clinic} · {date}")
    return "\n".join(lines)


def format_labs_summary(groups: list[dict], label: str) -> str:
    if not groups:
        return f"📊 За {label}: данных по показателям нет."
    total = sum(len(g["points"]) for g in groups)
    lines = [f"📊 Показатели за {label} (по {total} значениям)", "────────────"]
    for g in groups:
        pts = g["points"]
        vals = [p["value_num"] for p in pts]
        unit = html.escape(pts[-1].get("unit") or "")
        trend = " → ".join(str(v) for v in vals)
        last = pts[-1]
        marker = ""
        lo, hi, v = last.get("ref_low"), last.get("ref_high"), last["value_num"]
        if hi is not None and v > hi:
            marker = " ⬆️"
        elif lo is not None and v < lo:
            marker = " ⬇️"
        norm = ""
        if lo is not None and hi is not None:
            norm = f"  (норма {lo}–{hi})"
        elif hi is not None:
            norm = f"  (норма <{hi})"
        name = html.escape(g["analyte_name"])
        lines.append(f"{name}: {trend} {unit}{marker}{norm}")
    return "\n".join(lines)


