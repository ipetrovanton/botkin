"""Рендер карточек документов для Telegram (чистые функции, тестируемы без БД)."""
import html
import json

_PROBLEM_STATUSES = {"expired", "excluded", "suspended"}


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
