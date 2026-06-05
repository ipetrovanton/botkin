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


def get_document(document_id: int, user_id: int) -> dict | None:
    """Документ по id с проверкой принадлежности."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def get_document_status(document_id: int, user_id: int) -> str | None:
    """Текущий статус документа (для поллинга прогресса)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    return row["status"] if row else None


def get_adjacent_document_id(user_id: int, document_id: int, *, older: bool) -> int | None:
    """id соседнего документа по дате (тай-брейк по id), в пределах пользователя.

    older=True — старее текущего (предыдущий в ленте по убыванию даты);
    older=False — новее. Возвращает None, если соседа нет или документ чужой.
    Опирается на индекс по created_at вместо выгрузки всего списка id.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT created_at, id FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if not cur:
            return None
        if older:
            sql = (
                "SELECT id FROM documents WHERE user_id = ? "
                "AND (created_at < ? OR (created_at = ? AND id < ?)) "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            )
        else:
            sql = (
                "SELECT id FROM documents WHERE user_id = ? "
                "AND (created_at > ? OR (created_at = ? AND id > ?)) "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            )
        row = conn.execute(
            sql, (user_id, cur["created_at"], cur["created_at"], cur["id"])
        ).fetchone()
    return row["id"] if row else None


def count_documents(user_id: int, doc_type: str | None = None) -> int:
    sql = "SELECT COUNT(*) AS c FROM documents WHERE user_id = ?"
    params: list = [user_id]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    with get_conn() as conn:
        return conn.execute(sql, tuple(params)).fetchone()["c"]


def list_documents(user_id: int, doc_type: str | None = None,
                   limit: int = 7, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM documents WHERE user_id = ?"
    params: list = [user_id]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def documents_in_period(user_id: int, start, end, doc_type: str | None = None,
                        limit: int = 7, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM documents WHERE user_id = ? AND created_at >= ? AND created_at <= ?"
    params: list = [user_id, str(start), str(end)]
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def labs_in_period(user_id: int, start, end) -> list[dict]:
    """Показатели за период, сгруппированные по analyte_name, точки по времени."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT analyte_name, value_num, unit, ref_low, ref_high, taken_at "
            "FROM lab_results WHERE user_id = ? AND taken_at >= ? AND taken_at <= ? "
            "AND value_num IS NOT NULL ORDER BY analyte_name ASC, taken_at ASC",
            (user_id, str(start), str(end)),
        ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["analyte_name"], {"analyte_name": r["analyte_name"], "points": []})
        g["points"].append(dict(r))
    return list(groups.values())


def get_lab_results(document_id: int, limit: int | None = None) -> list[dict]:
    # Карточка документа показывает ВСЕ строки панели в порядке документа.
    # Дефолтного LIMIT нет: панель ОАК+СРБ (21 строка) обрезалась на LIMIT 20.
    # ORDER BY id ASC сохраняет порядок вставки (= порядок в документе).
    sql = (
        "SELECT analyte_name, value_num, value_text, unit, "
        "ref_low, ref_high, ref_operator, ref_text, "
        "analyte_canonical, loinc, nmu_code, analyte_group, "
        "match_status, unit_expected, unit_mismatch "
        "FROM lab_results WHERE document_id = ? ORDER BY id ASC"
    )
    params: tuple = (document_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (document_id, limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
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