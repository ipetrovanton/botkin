"""Методы записи документов: create, set_status, verify, delete, репарсинг."""
from __future__ import annotations

import sqlite3


class DocumentWriteMixin:
    """Примешивается к DocumentRepo; использует self.conn и self.user_id."""

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

    def delete(self, document_id: int) -> str | None:
        """Полное удаление документа с данными (в скоупе пользователя).

        Атомарно удаляет показатели, заключения и сам документ; возвращает
        source_path для удаления файла-исходника вызывающим, либо None,
        если документ не найден / чужой.
        """
        from .connection import transaction

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
        from .connection import transaction

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
