"""Репозиторий документов."""
from __future__ import annotations
from .base import BaseRepo


class DocumentRepo(BaseRepo):
    table = "documents"

    def create(self, source_path: str, doc_type: str = "unknown") -> int:
        cur = self.conn.execute(
            """
            INSERT INTO documents(user_id, doc_type, source_path, status)
            VALUES (?, ?, ?, 'received')
            """,
            (self.user_id, doc_type, source_path),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_status(
        self,
        document_id: int,
        status: str,
        raw_text: str | None = None,
        confidence: float | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE documents
            SET status = ?, raw_text = COALESCE(?, raw_text),
                confidence = COALESCE(?, confidence)
            WHERE id = ? AND user_id = ?
            """,
            (status, raw_text, confidence, document_id, self.user_id),
        )
        self.conn.commit()

    def get(self, document_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (self.user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]