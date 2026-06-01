"""Аналитические запросы к БД."""
from .connection import get_conn


def lab_dynamics(user_id: int, analyte_name: str, limit: int = 30) -> list[dict]:
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


def get_user_id(telegram_user_id: int) -> int | None:
    """Возвращает user_id или None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
    return row["id"] if row else None


def get_last_document(user_id: int) -> dict | None:
    """Последний документ пользователя."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_lab_results(document_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT analyte_name, value_num, unit, ref_low, ref_high "
            "FROM lab_results WHERE document_id = ? LIMIT ?",
            (document_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_prescriptions(document_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT drug_mnn, drug_trade, dose, frequency, duration_days "
            "FROM prescriptions WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_doctor_reports(document_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT diagnosis, recommendations_json, complaints_json, "
            "medications_json, doctor_name, department "
            "FROM doctor_reports WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]