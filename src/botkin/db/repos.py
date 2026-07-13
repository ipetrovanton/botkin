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

    ROLES = ("admin", "user")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_or_create(self, telegram_user_id: int) -> int:
        """Возвращает user_id по telegram_user_id, создаёт при необходимости.

        Бутстрап ролей: id из ADMIN_TELEGRAM_IDS получает роль admin и при создании,
        и при повторном входе (список в конфиге мог пополниться после регистрации).
        """
        from botkin.config import ADMIN_TELEGRAM_IDS

        is_admin = telegram_user_id in ADMIN_TELEGRAM_IDS
        row = self.conn.execute(
            "SELECT id, role FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        if row:
            if is_admin and row["role"] != "admin":
                self.set_role(row["id"], "admin")
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO users(telegram_user_id, role) VALUES (?, ?)",
            (telegram_user_id, "admin" if is_admin else "user"),
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

    def get(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, telegram_user_id, role, display_name, created_at "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def role_of(self, user_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["role"] if row else None

    def list_all(self) -> list[dict]:
        """Все пользователи со счётчиками данных — для экрана администратора."""
        rows = self.conn.execute(
            """
            SELECT u.id, u.telegram_user_id, u.role, u.display_name, u.created_at,
                   (SELECT COUNT(*) FROM documents d WHERE d.user_id = u.id) AS documents,
                   (SELECT COUNT(*) FROM lab_results l WHERE l.user_id = u.id) AS lab_results,
                   (SELECT COUNT(*) FROM doctor_reports r WHERE r.user_id = u.id) AS reports
            FROM users u ORDER BY u.id
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def create(
        self, telegram_user_id: int, display_name: str | None = None, role: str = "user",
    ) -> int:
        """Создание пользователя админом. IntegrityError по UNIQUE ловит вызывающий."""
        if role not in self.ROLES:
            raise ValueError(f"Недопустимая роль: {role}")
        cur = self.conn.execute(
            "INSERT INTO users(telegram_user_id, display_name, role) VALUES (?, ?, ?)",
            (telegram_user_id, display_name, role),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_role(self, user_id: int, role: str) -> None:
        # CHECK в мигрированных БД отсутствует — инвариант держим здесь.
        if role not in self.ROLES:
            raise ValueError(f"Недопустимая роль: {role}")
        self.conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        self.conn.commit()

    def set_display_name(self, user_id: int, display_name: str | None) -> None:
        self.conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id)
        )
        self.conn.commit()

    def admin_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'"
        ).fetchone()["c"]

    def delete_cascade(self, user_id: int) -> list[str]:
        """Удаляет пользователя со всеми данными; возвращает source_path документов —
        файлы-исходники удаляет вызывающий (доступ к хранилищу — забота API-слоя)."""
        paths = [
            r["source_path"]
            for r in self.conn.execute(
                "SELECT source_path FROM documents WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        with transaction(self.conn):
            for table in (
                "lab_results", "doctor_reports", "documents",
                "health_metrics", "health_activities", "health_accounts",
            ):
                self.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return paths


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

    def distinct_analytes(self) -> list[dict]:
        """Уникальные показатели для селектора динамики: каноничное имя, иначе исходное."""
        rows = self.conn.execute(
            "SELECT DISTINCT COALESCE(analyte_canonical, analyte_name) AS name "
            "FROM lab_results WHERE user_id = ? ORDER BY name ASC",
            (self.user_id,),
        ).fetchall()
        return [r["name"] for r in rows]


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


class HealthRepo(BaseRepo):
    """Подключённые источники здоровья, time-series метрик и активности."""

    table = "health_metrics"

    # --- аккаунты ---

    def upsert_account(
        self, provider: str, *, identifier: str | None = None,
        token_path: str | None = None, token_json: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO health_accounts(user_id, provider, identifier, token_path, token_json,
                   status, last_error)
               VALUES (?, ?, ?, ?, ?, 'connected', NULL)
               ON CONFLICT(user_id, provider) DO UPDATE SET
                   identifier = excluded.identifier,
                   token_path = COALESCE(excluded.token_path, token_path),
                   token_json = COALESCE(excluded.token_json, token_json),
                   status = 'connected', last_error = NULL""",
            (self.user_id, provider, identifier, token_path, token_json),
        )
        self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        return self.get_account(provider)["id"]

    def get_account(self, provider: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM health_accounts WHERE user_id = ? AND provider = ?",
            (self.user_id, provider),
        ).fetchone()
        return dict(row) if row else None

    def list_accounts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT provider, identifier, status, last_error, last_sync_at, created_at "
            "FROM health_accounts WHERE user_id = ? ORDER BY provider",
            (self.user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_synced(self, provider: str) -> None:
        self.conn.execute(
            "UPDATE health_accounts SET last_sync_at = CURRENT_TIMESTAMP, status = 'connected', "
            "last_error = NULL WHERE user_id = ? AND provider = ?",
            (self.user_id, provider),
        )
        self.conn.commit()

    def mark_error(self, provider: str, error: str) -> None:
        self.conn.execute(
            "UPDATE health_accounts SET status = 'error', last_error = ? "
            "WHERE user_id = ? AND provider = ?",
            (error[:500], self.user_id, provider),
        )
        self.conn.commit()

    def disconnect(self, provider: str) -> None:
        self.conn.execute(
            "UPDATE health_accounts SET status = 'disconnected', token_json = NULL "
            "WHERE user_id = ? AND provider = ?",
            (self.user_id, provider),
        )
        self.conn.commit()

    # --- метрики ---

    def save_metrics(self, rows: list[dict]) -> int:
        """Идемпотентный upsert точек: повторный синк дня перезаписывает значения."""
        with transaction(self.conn):
            for r in rows:
                self.conn.execute(
                    """INSERT INTO health_metrics(user_id, provider, metric, taken_at,
                           value_num, value_json, unit)
                       VALUES (:user_id, :provider, :metric, :taken_at,
                           :value_num, :value_json, :unit)
                       ON CONFLICT(user_id, provider, metric, taken_at) DO UPDATE SET
                           value_num = excluded.value_num,
                           value_json = excluded.value_json,
                           unit = excluded.unit""",
                    {"user_id": self.user_id, "value_num": None, "value_json": None,
                     "unit": None, **r},
                )
        return len(rows)

    def metrics_series(
        self, metric: str, *, date_from: str | None = None, date_to: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Точки одной метрики по времени (свежие, хронологический порядок)."""
        where = ["user_id = ?", "metric = ?"]
        params: list = [self.user_id, metric]
        if date_from:
            where.append("taken_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("taken_at <= ?")
            params.append(date_to)
        rows = self.conn.execute(
            f"SELECT provider, taken_at, value_num, value_json, unit FROM health_metrics "
            f"WHERE {' AND '.join(where)} ORDER BY taken_at DESC LIMIT ?",
            tuple(params + [limit]),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def distinct_metrics(self) -> list[dict]:
        """Метрики с числом точек и границами дат — для селектора кабинета."""
        rows = self.conn.execute(
            "SELECT metric, COUNT(*) AS points, MIN(taken_at) AS date_min, "
            "MAX(taken_at) AS date_max FROM health_metrics "
            "WHERE user_id = ? GROUP BY metric ORDER BY metric",
            (self.user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def daily_summary(self, date_from: str, date_to: str) -> list[dict]:
        """Дневные агрегаты по всем метрикам за период (для RAG-сводок и дашборда)."""
        rows = self.conn.execute(
            """SELECT metric, DATE(taken_at) AS day, COUNT(*) AS points,
                   ROUND(AVG(value_num), 1) AS avg, MIN(value_num) AS min,
                   MAX(value_num) AS max, unit
               FROM health_metrics
               WHERE user_id = ? AND taken_at >= ? AND taken_at <= ? AND value_num IS NOT NULL
               GROUP BY metric, DATE(taken_at) ORDER BY day, metric""",
            (self.user_id, date_from, date_to),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- активности ---

    def save_activities(self, rows: list[dict]) -> int:
        with transaction(self.conn):
            for r in rows:
                self.conn.execute(
                    """INSERT INTO health_activities(user_id, provider, external_id,
                           activity_type, name, started_at, duration_s, distance_m,
                           avg_hr, max_hr, calories, raw_json)
                       VALUES (:user_id, :provider, :external_id, :activity_type, :name,
                           :started_at, :duration_s, :distance_m, :avg_hr, :max_hr,
                           :calories, :raw_json)
                       ON CONFLICT(user_id, provider, external_id) DO UPDATE SET
                           activity_type = excluded.activity_type, name = excluded.name,
                           started_at = excluded.started_at, duration_s = excluded.duration_s,
                           distance_m = excluded.distance_m, avg_hr = excluded.avg_hr,
                           max_hr = excluded.max_hr, calories = excluded.calories,
                           raw_json = excluded.raw_json""",
                    {"user_id": self.user_id, "activity_type": None, "name": None,
                     "started_at": None, "duration_s": None, "distance_m": None,
                     "avg_hr": None, "max_hr": None, "calories": None, "raw_json": None, **r},
                )
        return len(rows)

    def list_activities(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT provider, external_id, activity_type, name, started_at, duration_s, "
            "distance_m, avg_hr, max_hr, calories FROM health_activities "
            "WHERE user_id = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (self.user_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        m = self.conn.execute(
            "SELECT COUNT(*) AS points, COUNT(DISTINCT metric) AS metrics, "
            "MIN(taken_at) AS date_min, MAX(taken_at) AS date_max "
            "FROM health_metrics WHERE user_id = ?",
            (self.user_id,),
        ).fetchone()
        a = self.conn.execute(
            "SELECT COUNT(*) AS c FROM health_activities WHERE user_id = ?",
            (self.user_id,),
        ).fetchone()["c"]
        return {**dict(m), "activities": a}
