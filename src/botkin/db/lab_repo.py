"""Репозиторий лабораторных показателей: вставка панелей, строчный CRUD, динамика."""
from __future__ import annotations

from datetime import datetime

from .base import BaseRepo
from .connection import transaction


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
        # id нужен форме верификации в кабинете — правка/удаление конкретной строки.
        sql = (
            "SELECT id, analyte_name, value_num, value_text, unit, "
            "ref_low, ref_high, ref_operator, ref_text, taken_at, "
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

    # --- строчный CRUD (верификация распознанного и админка) ---

    # Колонки, редактируемые через форму. Служебные (id, user_id, document_id,
    # created_at) и трассировочные *_raw намеренно вне списка.
    EDITABLE_COLUMNS = frozenset({
        "analyte_name", "value_num", "value_text", "unit",
        "ref_low", "ref_high", "ref_operator", "ref_text",
        "taken_at", "analyte_canonical", "analyte_group", "match_status",
    })

    def get_row(self, lab_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM lab_results WHERE id = ? AND user_id = ?",
            (lab_id, self.user_id),
        ).fetchone()
        return dict(row) if row else None

    def update_row(self, lab_id: int, fields: dict) -> bool:
        """Частичное обновление строки; False — строка не найдена/чужая."""
        cols = {k: v for k, v in fields.items() if k in self.EDITABLE_COLUMNS}
        if not cols:
            return False
        assignments = ", ".join(f"{c} = ?" for c in cols)
        cur = self.conn.execute(
            f"UPDATE lab_results SET {assignments} WHERE id = ? AND user_id = ?",
            (*cols.values(), lab_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_row(self, lab_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM lab_results WHERE id = ? AND user_id = ?",
            (lab_id, self.user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def insert_manual(self, document_id: int, fields: dict) -> int:
        """Ручное добавление показателя к документу (форма верификации/админка)."""
        cols = {k: v for k, v in fields.items() if k in self.EDITABLE_COLUMNS}
        cols.setdefault("match_status", "manual")
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        cur = self.conn.execute(
            f"INSERT INTO lab_results(document_id, user_id, {names}) "
            f"VALUES (?, ?, {marks})",
            (document_id, self.user_id, *cols.values()),
        )
        self.conn.commit()
        return cur.lastrowid

    def dynamics(self, analyte_name: str, limit: int = 30) -> list[dict]:
        """Серия точек одного показателя по времени (свежие, хронологический порядок).

        Сначала точное совпадение по COALESCE(canonical, name) — так селектор
        веб-кабинета (который отдаёт каноничные имена) находит и сырые строки
        ('HGB' с каноном 'Гемоглобин'), а «Глюкоза» не подмешивает «Глюкозу в моче».
        Если точного совпадения нет — LIKE-fallback для частичного ввода из бота
        (/dynamics холестер). DESC + reverse: при превышении limit остаются
        самые свежие замеры, а не самые ранние.
        """
        base = """
            SELECT lr.taken_at, lr.value_num, lr.unit, lr.ref_low, lr.ref_high
            FROM lab_results lr
            WHERE lr.user_id = ?
              AND {match}
              AND lr.value_num IS NOT NULL
            ORDER BY lr.taken_at DESC
            LIMIT ?
            """
        exact = base.format(
            match="LOWER(COALESCE(lr.analyte_canonical, lr.analyte_name)) = ?"
        )
        rows = self.conn.execute(
            exact, (self.user_id, analyte_name.lower(), limit)
        ).fetchall()
        if not rows:
            fuzzy = base.format(match="LOWER(lr.analyte_name) LIKE ?")
            rows = self.conn.execute(
                fuzzy, (self.user_id, f"%{analyte_name.lower()}%", limit)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def in_period(self, start: datetime, end: datetime) -> list[dict]:
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

    def distinct_analytes(self) -> list[dict]:
        """Уникальные показатели для селектора динамики: каноничное имя, иначе исходное."""
        rows = self.conn.execute(
            "SELECT DISTINCT COALESCE(analyte_canonical, analyte_name) AS name "
            "FROM lab_results WHERE user_id = ? ORDER BY name ASC",
            (self.user_id,),
        ).fetchall()
        return [r["name"] for r in rows]
