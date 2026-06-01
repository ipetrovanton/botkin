"""Аналитические запросы к БД."""
from .connection import get_conn


def lab_dynamics(
    user_id: int, analyte_name: str, limit: int = 30
) -> list[dict]:
    """Серия точек одного показателя по времени."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT lr.taken_at, lr.value_num, lr.unit, lr.ref_low, lr.ref_high
            FROM lab_results lr
            WHERE lr.user_id = ?
              AND LOWER(lr.analyte_name) LIKE ?
              AND lr.value_num IS NOT NULL
            ORDER BY lr.taken_at ASC
            LIMIT ?
            """,
            (user_id, f"%{analyte_name.lower()}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]