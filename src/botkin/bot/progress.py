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


async def poll_until_done(doc_id, get_status, edit, sleep, now,
                          interval: float = 2.0, timeout: float = 120.0):
    """Поллит статус, редактирует сообщение при смене стадии.

    Параметры-функции инъектируются для тестируемости:
      get_status() -> awaitable[str|None]; edit(text)->awaitable;
      sleep(sec)->awaitable; now()->float (монотонные секунды).
    Возвращает финальный статус (extracted/failed) или None при таймауте.
    """
    start = now()
    last_rendered = None
    while now() - start <= timeout:
        status = await get_status()
        if status and status != last_rendered:
            if is_terminal(status):
                return status
            await edit(render_progress(status, doc_id))
            last_rendered = status
        await sleep(interval)
    return None
