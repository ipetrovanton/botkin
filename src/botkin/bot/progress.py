"""Прогресс-бар обработки документа: рендер стадий + поллинг статуса."""

_STAGES = [
    ("received", "📥 Принято"),
    ("recognizing", "🔍 Распознаю текст"),
    ("normalizing", "🧩 Нормализую данные"),
    ("extracted", "✅ Готово"),
]
_ORDER = {name: i for i, (name, _) in enumerate(_STAGES)}

TERMINAL = {"extracted", "failed"}


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL


def render_progress(status: str, doc_id: int) -> str:
    """Текст прогресс-бара: пройденные — ✓, текущая — ●, будущие — без маркера."""
    cur = _ORDER.get(status, 0)
    lines = [f"⏳ Документ #{doc_id} — обрабатываю"]
    for i, (_, label) in enumerate(_STAGES):
        if i < cur:
            lines.append(f"{label} ✓")
        elif i == cur:
            lines.append(f"{label} ●")
        else:
            lines.append(label)
    return "\n".join(lines)
