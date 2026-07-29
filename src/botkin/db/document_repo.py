"""Репозиторий документов: CRUD, статусы, поиск, статистика."""
from __future__ import annotations

import sqlite3

from .base import BaseRepo
from .connection import transaction


class DocumentRepo(BaseRepo):
    table = "documents"

    # --- запись ---

    def create(
        self, source_path: str, doc_type: str = "unknown", file_sha256: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO documents(user_id, doc_type, source_path, status, file_sha256) "
            "VALUES (?, ?, ?, 'received', ?)",
            (self.user_id, doc_type, source_path, file_sha256),
        )
        self.conn.commit()
        return cur.lastrowid

    def find_duplicate_of(self, document_id: int) -> int | None:
        """Более ранний документ-дубликат: тот же файл (sha256) либо тот же бланк
        (совпадают doc_type, title, clinic). Возвращает id старого документа."""
        doc = self.conn.execute(
            "SELECT doc_type, title, clinic, file_sha256 FROM documents "
            "WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        if doc is None:
            return None
        row = self.conn.execute(
            """
            SELECT id FROM documents
            WHERE user_id = ? AND id != ? AND status = 'extracted'
              AND (
                (file_sha256 IS NOT NULL AND file_sha256 = ?)
                OR (doc_type = ? AND title IS NOT NULL AND title = ?
                    AND COALESCE(clinic, '') = COALESCE(?, ''))
              )
            ORDER BY id ASC LIMIT 1
            """,
            (self.user_id, document_id, doc["file_sha256"],
             doc["doc_type"], doc["title"], doc["clinic"]),
        ).fetchone()
        return row["id"] if row else None

    def extracted_rows_count(self, document_id: int) -> int:
        """Суммарное число извлечённых записей документа (показатели + заключения)."""
        labs = self.conn.execute(
            "SELECT COUNT(*) AS c FROM lab_results WHERE document_id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()["c"]
        reports = self.conn.execute(
            "SELECT COUNT(*) AS c FROM doctor_reports WHERE document_id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()["c"]
        return labs + reports

    def set_status(self, document_id: int, status: str) -> None:
        """Меняет стадию + фиксирует время входа в неё и EMA длительности предыдущей.

        stage_started_at нужен достоверному прогресс-бару (elapsed внутри стадии),
        EMA — оценке ожидаемой длительности следующих прогонов (см. progress_model).
        """
        import time as _time

        from botkin.pipeline.progress_model import StageDurationStore

        now = _time.time()
        prev = self.conn.execute(
            "SELECT status, stage_started_at FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        if prev and prev["status"] and prev["stage_started_at"]:
            duration = now - float(prev["stage_started_at"])
            if 0 < duration < 3600:  # аномалии (час+) не портят EMA
                StageDurationStore(self.conn).record(prev["status"], duration)
        self.conn.execute(
            "UPDATE documents SET status = ?, stage_started_at = ? WHERE id = ? AND user_id = ?",
            (status, now, document_id, self.user_id),
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

    def mark_verified(self, document_id: int) -> bool:
        """Пользователь подтвердил корректность распознанных данных."""
        cur = self.conn.execute(
            "UPDATE documents SET verified_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def clear_verified(self, document_id: int) -> None:
        """Сброс отметки после любой правки данных — подтверждение относилось к старой версии."""
        self.conn.execute(
            "UPDATE documents SET verified_at = NULL WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
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

    def get_progress_row(self, document_id: int) -> dict | None:
        """Статус + время входа в стадию — для оценки прогресса и ETA."""
        row = self.conn.execute(
            "SELECT status, stage_started_at FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        return dict(row) if row else None

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

    # --- фильтрованный поиск для веб-кабинета ---

    def search(
        self,
        *,
        doc_type: str | None = None,
        clinic: str | None = None,
        doctor: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Фильтрованный список документов + total для пагинации.

        Фильтры: тип, клиника, врач (EXISTS по doctor_reports), диапазон created_at,
        статус, полнотекстовый поиск по title/clinic. Все параметры — плейсхолдеры.
        """
        where = ["user_id = ?"]
        params: list = [self.user_id]
        if doc_type:
            where.append("doc_type = ?")
            params.append(doc_type)
        if clinic:
            where.append("clinic = ?")
            params.append(clinic)
        if status:
            where.append("status = ?")
            params.append(status)
        if date_from:
            where.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            # created_at — TIMESTAMP, лексикографически больше голой даты
            # 'YYYY-MM-DD'; строгое `< date_to + 1 день` включает весь день.
            where.append("created_at < date(?, '+1 day')")
            params.append(date_to)
        if q:
            where.append("(LOWER(title) LIKE ? OR LOWER(clinic) LIKE ?)")
            like = f"%{q.lower()}%"
            params += [like, like]
        if doctor:
            # Документ-заключение конкретного врача: EXISTS по doctor_reports.
            where.append(
                "EXISTS (SELECT 1 FROM doctor_reports r "
                "WHERE r.document_id = documents.id AND r.user_id = documents.user_id "
                "AND r.doctor_name = ?)"
            )
            params.append(doctor)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM documents WHERE {clause}", tuple(params)
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"SELECT * FROM documents WHERE {clause} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        ).fetchall()
        return [dict(r) for r in rows], total

    def distinct_clinics(self) -> list[str]:
        """Уникальные клиники пользователя (для селектора фильтра)."""
        rows = self.conn.execute(
            "SELECT DISTINCT clinic FROM documents "
            "WHERE user_id = ? AND clinic IS NOT NULL AND clinic <> '' ORDER BY clinic",
            (self.user_id,),
        ).fetchall()
        return [r["clinic"] for r in rows]

    def date_range(self) -> tuple[str | None, str | None]:
        """Мин/макс created_at — границы фильтра по умолчанию."""
        row = self.conn.execute(
            "SELECT MIN(created_at) AS lo, MAX(created_at) AS hi FROM documents WHERE user_id = ?",
            (self.user_id,),
        ).fetchone()
        return row["lo"], row["hi"]

    def stats(self) -> dict:
        """Сводка для дашборда: счётчики по типу/статусу + последний документ."""
        by_type = {
            r["doc_type"]: r["c"]
            for r in self.conn.execute(
                "SELECT doc_type, COUNT(*) AS c FROM documents WHERE user_id = ? GROUP BY doc_type",
                (self.user_id,),
            ).fetchall()
        }
        by_status = {
            r["status"]: r["c"]
            for r in self.conn.execute(
                "SELECT status, COUNT(*) AS c FROM documents WHERE user_id = ? GROUP BY status",
                (self.user_id,),
            ).fetchall()
        }
        last = self.conn.execute(
            "SELECT id, doc_type, title, clinic, created_at, status "
            "FROM documents WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (self.user_id,),
        ).fetchone()
        return {
            "total": sum(by_type.values()),
            "by_type": by_type,
            "by_status": by_status,
            "last": dict(last) if last else None,
        }

    def delete(self, document_id: int) -> str | None:
        """Полное удаление документа с данными (в скоупе пользователя).

        Атомарно удаляет показатели, заключения и сам документ; возвращает
        source_path для удаления файла-исходника вызывающим, либо None,
        если документ не найден / чужой.
        """
        row = self.conn.execute(
            "SELECT source_path FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        if row is None:
            return None
        with transaction(self.conn):
            self.conn.execute(
                "DELETE FROM lab_results WHERE document_id = ? AND user_id = ?",
                (document_id, self.user_id),
            )
            self.conn.execute(
                "DELETE FROM doctor_reports WHERE document_id = ? AND user_id = ?",
                (document_id, self.user_id),
            )
            self.conn.execute(
                "DELETE FROM documents WHERE id = ? AND user_id = ?",
                (document_id, self.user_id),
            )
        return row["source_path"]

    def clear_extracted_data(self, document_id: int) -> None:
        """Очистка извлечённых данных перед повторным распознаванием (репарсингом)."""
        with transaction(self.conn):
            self.conn.execute(
                "DELETE FROM lab_results WHERE document_id = ? AND user_id = ?",
                (document_id, self.user_id),
            )
            self.conn.execute(
                "DELETE FROM doctor_reports WHERE document_id = ? AND user_id = ?",
                (document_id, self.user_id),
            )
            self.conn.execute(
                "UPDATE documents SET raw_extraction = NULL, status = 'received' "
                "WHERE id = ? AND user_id = ?",
                (document_id, self.user_id),
            )

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
