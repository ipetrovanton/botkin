"""Единый слой доступа к данным.

Все обращения к БД идут через репозитории — один контракт вместо параллельных
queries/inline-SQL. Тенант-скоуп (`WHERE user_id = ?`) — инвариант класса: репозиторий
получает user_id в конструкторе и подставляет его во все запросы. Две операции точки
входа pipeline честно вне скоупа (владелец ещё не прочитан / глобальный обработчик сбоя) —
оформлены как @staticmethod-люки `get_by_id`/`mark_failed`.
"""
from __future__ import annotations

import sqlite3

from .connection import transaction


class BaseRepo:
    """Все репозитории получают user_id в конструкторе."""

    table: str = ""

    def __init__(self, conn: sqlite3.Connection, user_id: int):
        if user_id <= 0:
            raise ValueError("user_id обязателен и должен быть > 0")
        self.conn = conn
        self.user_id = user_id


class UserRepo:
    table = "users"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_or_create(self, telegram_user_id: int) -> int:
        """Возвращает user_id по telegram_user_id, создаёт при необходимости."""
        row = self.conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO users(telegram_user_id) VALUES (?)",
            (telegram_user_id,),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_id(self, telegram_user_id: int) -> int | None:
        """user_id по telegram_user_id или None (без создания)."""
        row = self.conn.execute(
            "SELECT id FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        return row["id"] if row else None


class DocumentRepo(BaseRepo):
    table = "documents"

    # --- запись ---

    def create(self, source_path: str, doc_type: str = "unknown") -> int:
        cur = self.conn.execute(
            "INSERT INTO documents(user_id, doc_type, source_path, status) "
            "VALUES (?, ?, ?, 'received')",
            (self.user_id, doc_type, source_path),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_status(self, document_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE documents SET status = ? WHERE id = ? AND user_id = ?",
            (status, document_id, self.user_id),
        )
        self.conn.commit()

    def set_metadata(self, document_id: int, title: str | None, clinic: str | None) -> None:
        self.conn.execute(
            "UPDATE documents SET title = ?, clinic = ? WHERE id = ? AND user_id = ?",
            (title, clinic, document_id, self.user_id),
        )
        self.conn.commit()

    def set_doc_type(self, document_id: int, doc_type: str) -> None:
        self.conn.execute(
            "UPDATE documents SET doc_type = ? WHERE id = ? AND user_id = ?",
            (doc_type, document_id, self.user_id),
        )
        self.conn.commit()

    def save_raw_extraction(self, document_id: int, payload: str) -> None:
        """Сырой ответ модели (до нормализации) — гарантия восстановимости."""
        self.conn.execute(
            "UPDATE documents SET raw_extraction = ? WHERE id = ? AND user_id = ?",
            (payload, document_id, self.user_id),
        )
        self.conn.commit()

    def claim_delivery(self, document_id: int) -> bool:
        """Атомарно помечает доставку; True если захватил первым."""
        cur = self.conn.execute(
            "UPDATE documents SET delivered_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND delivered_at IS NULL",
            (document_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- чтение ---

    def get(self, document_id: int) -> dict | None:
        """Документ по id с проверкой принадлежности."""
        row = self.conn.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        return dict(row) if row else None

    def get_status(self, document_id: int) -> str | None:
        """Текущий статус документа (для поллинга прогресса)."""
        row = self.conn.execute(
            "SELECT status FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        return row["status"] if row else None

    def get_last(self) -> dict | None:
        """Последний документ пользователя."""
        row = self.conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.user_id,),
        ).fetchone()
        return dict(row) if row else None

    def adjacent_id(self, document_id: int, *, older: bool) -> int | None:
        """id соседнего документа по дате (тай-брейк по id), в пределах пользователя.

        older=True — старее текущего (предыдущий в ленте по убыванию даты);
        older=False — новее. Возвращает None, если соседа нет или документ чужой.
        Опирается на индекс по created_at вместо выгрузки всего списка id.
        """
        cur = self.conn.execute(
            "SELECT created_at, id FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
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
        row = self.conn.execute(
            sql, (self.user_id, cur["created_at"], cur["created_at"], cur["id"])
        ).fetchone()
        return row["id"] if row else None

    def count(self, doc_type: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS c FROM documents WHERE user_id = ?"
        params: list = [self.user_id]
        if doc_type:
            sql += " AND doc_type = ?"
            params.append(doc_type)
        return self.conn.execute(sql, tuple(params)).fetchone()["c"]

    def list(self, doc_type: str | None = None, limit: int = 7, offset: int = 0) -> list[dict]:
        sql = "SELECT * FROM documents WHERE user_id = ?"
        params: list = [self.user_id]
        if doc_type:
            sql += " AND doc_type = ?"
            params.append(doc_type)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def in_period(self, start, end, doc_type: str | None = None,
                  limit: int = 7, offset: int = 0) -> list[dict]:
        sql = "SELECT * FROM documents WHERE user_id = ? AND created_at >= ? AND created_at <= ?"
        params: list = [self.user_id, str(start), str(end)]
        if doc_type:
            sql += " AND doc_type = ?"
            params.append(doc_type)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    # --- люки вне user-скоупа (точка входа pipeline) ---

    @staticmethod
    def get_by_id(conn: sqlite3.Connection, document_id: int) -> dict | None:
        """Документ по одному id — для входа pipeline, где владелец ещё не прочитан."""
        row = conn.execute(
            "SELECT id, user_id, source_path FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def mark_failed(conn: sqlite3.Connection, document_id: int) -> None:
        """Пометить документ failed по id.

        Без user_id: вызывается в т.ч. из глобального обработчика, который ловит сбой
        ещё до того, как из БД прочитан владелец документа. Пометка статуса по id
        безопасна — данные не читаются, только переключается статус.
        """
        conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
        conn.commit()


class LabRepo(BaseRepo):
    table = "lab_results"

    def save_results(self, rows: list[dict]) -> None:
        """Атомарная вставка панели: либо все строки, либо ни одной.

        Принимает уже подготовленные (нормализованные) словари полей — нормализация
        живёт в pipeline-стадии, репозиторий лишь персистит.
        """
        with transaction(self.conn):
            for r in rows:
                self.conn.execute(
                    """INSERT INTO lab_results(document_id, user_id, analyte_code, analyte_name,
                       value_num, value_text, unit, ref_low, ref_high, ref_operator, ref_text,
                       taken_at, source_table_cell, value_raw, unit_raw, taken_at_raw,
                       analyte_canonical, loinc, nmu_code, analyte_group, match_status,
                       unit_expected, unit_mismatch)
                       VALUES (:document_id, :user_id, :analyte_code, :analyte_name,
                       :value_num, :value_text, :unit, :ref_low, :ref_high, :ref_operator, :ref_text,
                       :taken_at, :source_table_cell, :value_raw, :unit_raw, :taken_at_raw,
                       :analyte_canonical, :loinc, :nmu_code, :analyte_group, :match_status,
                       :unit_expected, :unit_mismatch)""",
                    r,
                )

    def for_document(self, document_id: int, limit: int | None = None) -> list[dict]:
        # Карточка документа показывает ВСЕ строки панели в порядке документа.
        # Дефолтного LIMIT нет: панель ОАК+СРБ (21 строка) обрезалась на LIMIT 20.
        # ORDER BY id ASC сохраняет порядок вставки (= порядок в документе).
        sql = (
            "SELECT analyte_name, value_num, value_text, unit, "
            "ref_low, ref_high, ref_operator, ref_text, "
            "analyte_canonical, loinc, nmu_code, analyte_group, "
            "match_status, unit_expected, unit_mismatch "
            "FROM lab_results WHERE document_id = ? AND user_id = ? ORDER BY id ASC"
        )
        params: tuple = (document_id, self.user_id)
        if limit is not None:
            sql += " LIMIT ?"
            params = (document_id, self.user_id, limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def dynamics(self, analyte_name: str, limit: int = 30) -> list[dict]:
        """Серия точек одного показателя по времени."""
        rows = self.conn.execute(
            """
            SELECT lr.taken_at, lr.value_num, lr.unit, lr.ref_low, lr.ref_high
            FROM lab_results lr
            WHERE lr.user_id = ?
              AND LOWER(lr.analyte_name) LIKE ?
              AND lr.value_num IS NOT NULL
            ORDER BY lr.taken_at ASC
            LIMIT ?
            """,
            (self.user_id, f"%{analyte_name.lower()}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def in_period(self, start, end) -> list[dict]:
        """Показатели за период, сгруппированные по analyte_name, точки по времени."""
        rows = self.conn.execute(
            "SELECT analyte_name, value_num, unit, ref_low, ref_high, taken_at "
            "FROM lab_results WHERE user_id = ? AND taken_at >= ? AND taken_at <= ? "
            "AND value_num IS NOT NULL ORDER BY analyte_name ASC, taken_at ASC",
            (self.user_id, str(start), str(end)),
        ).fetchall()
        groups: dict[str, dict] = {}
        for r in rows:
            g = groups.setdefault(
                r["analyte_name"], {"analyte_name": r["analyte_name"], "points": []}
            )
            g["points"].append(dict(r))
        return list(groups.values())


class ReportRepo(BaseRepo):
    table = "doctor_reports"

    def save(self, rows: list[dict]) -> None:
        """Атомарная вставка заключений врача."""
        with transaction(self.conn):
            for r in rows:
                self.conn.execute(
                    """INSERT INTO doctor_reports(document_id, user_id, diagnosis,
                       recommendations_json, complaints_json, anamnesis, medications_json,
                       medications_normalized_json,
                       visit_date, doctor_name, department)
                       VALUES (:document_id, :user_id, :diagnosis,
                       :recommendations_json, :complaints_json, :anamnesis, :medications_json,
                       :medications_normalized_json,
                       :visit_date, :doctor_name, :department)""",
                    r,
                )

    def for_document(self, document_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT diagnosis, recommendations_json, complaints_json, "
            "medications_json, doctor_name, department "
            "FROM doctor_reports WHERE document_id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchall()
        return [dict(r) for r in rows]
