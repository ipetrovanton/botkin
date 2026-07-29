"""Репозиторий заключений врачей: вставка, CRUD, фильтр по периоду."""
from __future__ import annotations

from .base import BaseRepo
from .connection import transaction


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
            "SELECT id, diagnosis, recommendations_json, complaints_json, "
            "medications_json, doctor_name, department, visit_date "
            "FROM doctor_reports WHERE document_id = ? AND user_id = ?",
            (document_id, self.user_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # Колонки, редактируемые формой верификации. JSON-списки сериализует API-слой.
    EDITABLE_COLUMNS = frozenset({
        "diagnosis", "recommendations_json", "complaints_json",
        "medications_json", "doctor_name", "department", "visit_date",
    })

    def update_row(self, report_id: int, fields: dict) -> bool:
        """Частичное обновление заключения; False — не найдено/чужое."""
        cols = {k: v for k, v in fields.items() if k in self.EDITABLE_COLUMNS}
        if not cols:
            return False
        assignments = ", ".join(f"{c} = ?" for c in cols)
        cur = self.conn.execute(
            f"UPDATE doctor_reports SET {assignments} WHERE id = ? AND user_id = ?",
            (*cols.values(), report_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def distinct_doctors(self) -> list[dict]:
        """Уникальные врачи с отделением — для селектора фильтра."""
        rows = self.conn.execute(
            "SELECT DISTINCT doctor_name, department FROM doctor_reports "
            "WHERE user_id = ? AND doctor_name IS NOT NULL AND doctor_name <> '' "
            "ORDER BY doctor_name",
            (self.user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def for_period(
        self, *, date_from: str | None = None, date_to: str | None = None,
        doctor: str | None = None, clinic: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Заключения за период с клиникой из documents (JOIN) для отображения в ленте.

        Дата — по visit_date заключения (дата приёма), клиника — по documents.clinic.
        """
        where = ["r.user_id = ?"]
        params: list = [self.user_id]
        if date_from:
            where.append("r.visit_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("r.visit_date <= ?")
            params.append(date_to)
        if doctor:
            where.append("r.doctor_name = ?")
            params.append(doctor)
        if clinic:
            where.append("d.clinic = ?")
            params.append(clinic)
        clause = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM doctor_reports r "
            f"LEFT JOIN documents d ON d.id = r.document_id WHERE {clause}",
            tuple(params),
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"SELECT r.id, r.document_id, r.diagnosis, r.doctor_name, r.department, "
            f"r.visit_date, r.recommendations_json, r.medications_json, d.clinic "
            f"FROM doctor_reports r LEFT JOIN documents d ON d.id = r.document_id "
            f"WHERE {clause} ORDER BY r.visit_date DESC, r.id DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        ).fetchall()
        return [dict(r) for r in rows], total
