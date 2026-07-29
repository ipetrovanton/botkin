"""Репозиторий здоровья: аккаунты, time-series метрик, активности."""
from __future__ import annotations

from .base import BaseRepo
from .connection import transaction


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
            "SELECT provider, identifier, status, last_error, last_sync_at, "
            "sync_interval_hours, created_at "
            "FROM health_accounts WHERE user_id = ? ORDER BY provider",
            (self.user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_sync_interval(self, provider: str, hours: int | None) -> bool:
        """Частота автосинка; None = только вручную."""
        cur = self.conn.execute(
            "UPDATE health_accounts SET sync_interval_hours = ? "
            "WHERE user_id = ? AND provider = ?",
            (hours, self.user_id, provider),
        )
        self.conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def accounts_due_for_sync(conn) -> list[dict]:
        """Аккаунты, чей интервал автосинка истёк (для планировщика; все пользователи).

        Сравнение на стороне SQLite: last_sync_at хранится в UTC (CURRENT_TIMESTAMP),
        поэтому и сравниваем с datetime('now'). NULL last_sync_at = ни разу не синкали — пора."""
        rows = conn.execute(
            "SELECT user_id, provider FROM health_accounts "
            "WHERE status = 'connected' AND sync_interval_hours IS NOT NULL "
            "AND (last_sync_at IS NULL OR "
            "     datetime(last_sync_at, '+' || sync_interval_hours || ' hours') "
            "       <= datetime('now'))"
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
