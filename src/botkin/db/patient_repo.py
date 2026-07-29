"""Репозиторий пациента: профиль тела, жалобы, текущие препараты."""
from __future__ import annotations

from .base import BaseRepo


class PatientRepo(BaseRepo):
    """Формы пациента: профиль тела (1:1), жалобы и текущие препараты (история)."""

    table = "patient_profile"

    PROFILE_COLUMNS = frozenset({
        "sex", "birth_date", "height_cm", "weight_kg",
        "blood_type", "allergies", "chronic_conditions",
        "latitude", "longitude",
    })

    # --- профиль тела ---

    def get_profile(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM patient_profile WHERE user_id = ?", (self.user_id,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_profile(self, fields: dict) -> dict:
        """Частичное обновление: непереданные поля не трогаются."""
        cols = {k: v for k, v in fields.items() if k in self.PROFILE_COLUMNS}
        if cols:
            names = ", ".join(cols)
            marks = ", ".join("?" for _ in cols)
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
            self.conn.execute(
                f"INSERT INTO patient_profile(user_id, {names}) VALUES (?, {marks}) "
                f"ON CONFLICT(user_id) DO UPDATE SET {updates}, "
                "updated_at = CURRENT_TIMESTAMP",
                (self.user_id, *cols.values()),
            )
            self.conn.commit()
        return self.get_profile() or {"user_id": self.user_id}

    # --- жалобы ---

    def list_complaints(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, text, created_at FROM patient_complaints "
            "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (self.user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_complaint(self, text: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO patient_complaints(user_id, text) VALUES (?, ?)",
            (self.user_id, text),
        )
        self.conn.commit()
        return cur.lastrowid

    def delete_complaint(self, complaint_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM patient_complaints WHERE id = ? AND user_id = ?",
            (complaint_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- текущие препараты ---

    def list_medications(self, active_only: bool = False) -> list[dict]:
        sql = (
            "SELECT id, name, dosage, schedule, is_active, created_at "
            "FROM patient_medications WHERE user_id = ?"
        )
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY is_active DESC, created_at DESC, id DESC"
        rows = self.conn.execute(sql, (self.user_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_medication(self, name: str, dosage: str | None, schedule: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO patient_medications(user_id, name, dosage, schedule) "
            "VALUES (?, ?, ?, ?)",
            (self.user_id, name, dosage, schedule),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_medication_active(self, med_id: int, is_active: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE patient_medications SET is_active = ? WHERE id = ? AND user_id = ?",
            (1 if is_active else 0, med_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_medication(self, med_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM patient_medications WHERE id = ? AND user_id = ?",
            (med_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0
