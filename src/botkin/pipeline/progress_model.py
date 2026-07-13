"""Достоверная модель прогресса обработки документа.

Проблема: стадии pipeline (recognizing/normalizing) занимают от 20 секунд до 5+ минут
в зависимости от модели и документа. Статический маппинг «стадия → процент» замирает
на всё время VLM-инференса, и пользователь не отличает «модель думает» от «модель упала».

Решение — оценка процента по историческим длительностям стадий:
  1. Длительности прошлых прогонов каждой стадии хранятся как EMA (экспоненциальное
     скользящее среднее) в таблице stage_durations. EMA адаптируется при смене модели.
  2. Прогресс внутри стадии: elapsed / expected, обрезанный на 0.95 — бар движется
     всё время инференса, но не достигает конца стадии до её фактического завершения.
  3. Общий процент = базовый вес пройденных стадий + вес текущей × её внутренний прогресс.
  4. ETA = ожидаемое суммарное время − прошедшее (не меньше 0).

Heartbeat: сам факт ответа /status с растущим processing_s говорит фронту, что pipeline
жив (стадия зафиксирована в БД, процесс не упал — иначе статус стал бы failed).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# Веса стадий в общем прогрессе (сумма = 1.0). Получены из медианных длительностей
# бенчмарков qwen3-vl:8b-instruct: classify ≈ 15-25% времени, extract ≈ 70-80%.
_STAGE_WEIGHTS: dict[str, float] = {
    "received": 0.02,
    "recognizing": 0.28,
    "normalizing": 0.65,
    "extracted": 0.05,
}
_STAGE_ORDER = ["received", "recognizing", "normalizing", "extracted"]

# Стартовые ожидания длительности стадий (секунды) — до накопления статистики.
# Взяты из бенчмарка e2e: classify ~20s, extract ~70s на qwen3-vl:8b-instruct.
_DEFAULT_EXPECTED_S: dict[str, float] = {
    "received": 1.0,
    "recognizing": 25.0,
    "normalizing": 75.0,
    "extracted": 1.0,
}

# Коэффициент EMA: новое значение весит 30% — быстро адаптируется к смене модели,
# но одиночный аномальный документ не ломает оценку.
_EMA_ALPHA = 0.3

# Прогресс внутри стадии не достигает 1.0, пока стадия фактически не сменилась:
# «бар движется, но не врёт о завершении».
_INTRA_STAGE_CAP = 0.95


@dataclass
class ProgressEstimate:
    """Оценка прогресса для отдачи фронту."""
    status: str
    percent: int          # 0-100, монотонно растёт
    eta_seconds: int      # оценка оставшегося времени, 0 если неизвестно
    stage_elapsed_s: float
    alive: bool           # heartbeat: pipeline жив (не failed и стадия известна)


class StageDurationStore:
    """EMA-хранилище длительностей стадий поверх произвольного соединения sqlite."""

    _DDL = (
        "CREATE TABLE IF NOT EXISTS stage_durations ("
        "  stage TEXT PRIMARY KEY,"
        "  ema_seconds REAL NOT NULL,"
        "  samples INTEGER NOT NULL DEFAULT 0"
        ")"
    )

    def __init__(self, conn) -> None:
        self.conn = conn
        conn.execute(self._DDL)
        conn.commit()

    def expected(self, stage: str) -> float:
        row = self.conn.execute(
            "SELECT ema_seconds FROM stage_durations WHERE stage = ?", (stage,)
        ).fetchone()
        if row:
            return float(row["ema_seconds"] if hasattr(row, "keys") else row[0])
        return _DEFAULT_EXPECTED_S.get(stage, 30.0)

    def record(self, stage: str, duration_s: float) -> None:
        """Обновляет EMA стадии фактической длительностью завершённого прогона."""
        row = self.conn.execute(
            "SELECT ema_seconds, samples FROM stage_durations WHERE stage = ?", (stage,)
        ).fetchone()
        if row:
            prev = float(row["ema_seconds"] if hasattr(row, "keys") else row[0])
            samples = int(row["samples"] if hasattr(row, "keys") else row[1])
            ema = prev + _EMA_ALPHA * (duration_s - prev)
            self.conn.execute(
                "UPDATE stage_durations SET ema_seconds = ?, samples = ? WHERE stage = ?",
                (ema, samples + 1, stage),
            )
        else:
            self.conn.execute(
                "INSERT INTO stage_durations (stage, ema_seconds, samples) VALUES (?, ?, 1)",
                (stage, duration_s),
            )
        self.conn.commit()


def estimate_progress(
    status: str,
    stage_started_at: float | None,
    store: StageDurationStore,
    now: float | None = None,
) -> ProgressEstimate:
    """Оценивает процент и ETA по текущей стадии и её ожидаемой длительности.

    stage_started_at — unix-время входа в текущую стадию (см. documents.stage_started_at).
    Если оно неизвестно (legacy-строки) — возвращаем статический процент начала стадии.
    """
    now = time.time() if now is None else now

    if status == "failed":
        return ProgressEstimate(status, 0, 0, 0.0, alive=False)
    if status == "extracted":
        return ProgressEstimate(status, 100, 0, 0.0, alive=True)

    stage = status if status in _STAGE_ORDER else "recognizing"
    idx = _STAGE_ORDER.index(stage)
    base = sum(_STAGE_WEIGHTS[s] for s in _STAGE_ORDER[:idx])

    elapsed = max(0.0, now - stage_started_at) if stage_started_at else 0.0
    expected = store.expected(stage)
    intra = min(elapsed / expected, _INTRA_STAGE_CAP) if expected > 0 else 0.0

    percent = int(round((base + _STAGE_WEIGHTS[stage] * intra) * 100))

    remaining_current = max(0.0, expected - elapsed)
    remaining_future = sum(store.expected(s) for s in _STAGE_ORDER[idx + 1:-1])
    eta = int(round(remaining_current + remaining_future))

    return ProgressEstimate(
        status=status,
        percent=min(percent, 99),  # 100 — только при extracted
        eta_seconds=eta,
        stage_elapsed_s=round(elapsed, 1),
        alive=True,
    )
