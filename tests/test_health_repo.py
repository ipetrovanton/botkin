"""Тесты HealthRepo: аккаунты источников, идемпотентный upsert метрик, серии, агрегаты."""
from botkin.db.connection import get_conn
from botkin.db.repos import HealthRepo, UserRepo


def _repo(conn):
    uid = UserRepo(conn).get_or_create(42)
    return HealthRepo(conn, uid)


def test_upsert_account_idempotent(set_test_db):
    with get_conn() as conn:
        repo = _repo(conn)
        repo.upsert_account("garmin", identifier="a@b.c", token_path="/tokens/1")
        repo.upsert_account("garmin", identifier="a@b.c", token_path="/tokens/1")
        accounts = repo.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["provider"] == "garmin"
    assert accounts[0]["status"] == "connected"


def test_upsert_account_reconnect_resets_error(set_test_db):
    with get_conn() as conn:
        repo = _repo(conn)
        repo.upsert_account("garmin", identifier="a@b.c")
        repo.mark_error("garmin", "boom")
        assert repo.get_account("garmin")["status"] == "error"
        repo.upsert_account("garmin", identifier="a@b.c")
        account = repo.get_account("garmin")
    assert account["status"] == "connected"
    assert account["last_error"] is None


def test_save_metrics_idempotent(set_test_db):
    """Повторный синк того же дня перезаписывает значение, не плодит дубли."""
    row = {"provider": "garmin", "metric": "resting_heart_rate",
           "taken_at": "2026-07-01", "value_num": 60.0, "unit": "уд/мин"}
    with get_conn() as conn:
        repo = _repo(conn)
        repo.save_metrics([row])
        repo.save_metrics([{**row, "value_num": 58.0}])
        series = repo.metrics_series("resting_heart_rate")
    assert len(series) == 1
    assert series[0]["value_num"] == 58.0


def test_metrics_series_chronological_with_range(set_test_db):
    rows = [{"provider": "garmin", "metric": "steps", "taken_at": f"2026-07-0{d}",
             "value_num": d * 1000.0, "unit": "шагов"} for d in (3, 1, 2)]
    with get_conn() as conn:
        repo = _repo(conn)
        repo.save_metrics(rows)
        series = repo.metrics_series("steps", date_from="2026-07-02")
    assert [p["taken_at"] for p in series] == ["2026-07-02", "2026-07-03"]


def test_daily_summary_aggregates(set_test_db):
    rows = [
        {"provider": "garmin", "metric": "heart_rate",
         "taken_at": f"2026-07-01 10:0{i}:00", "value_num": v, "unit": "уд/мин"}
        for i, v in enumerate((60.0, 80.0, 100.0))
    ]
    with get_conn() as conn:
        repo = _repo(conn)
        repo.save_metrics(rows)
        summary = repo.daily_summary("2026-07-01", "2026-07-01 23:59:59")
    assert len(summary) == 1
    day = summary[0]
    assert day["points"] == 3 and day["min"] == 60.0 and day["max"] == 100.0
    assert day["avg"] == 80.0


def test_save_activities_idempotent(set_test_db):
    act = {"provider": "garmin", "external_id": "123", "activity_type": "running",
           "name": "Пробежка", "started_at": "2026-07-01 07:00:00",
           "duration_s": 1800.0, "distance_m": 5000.0, "avg_hr": 145.0}
    with get_conn() as conn:
        repo = _repo(conn)
        repo.save_activities([act])
        repo.save_activities([{**act, "name": "Пробежка утром"}])
        items = repo.list_activities()
    assert len(items) == 1
    assert items[0]["name"] == "Пробежка утром"


def test_metrics_scoped_by_user(set_test_db):
    """Тенант-скоуп: метрики одного пользователя не видны другому."""
    with get_conn() as conn:
        uid1 = UserRepo(conn).get_or_create(42)
        uid2 = UserRepo(conn).get_or_create(43)
        HealthRepo(conn, uid1).save_metrics([{
            "provider": "garmin", "metric": "steps", "taken_at": "2026-07-01",
            "value_num": 5000.0, "unit": "шагов",
        }])
        assert HealthRepo(conn, uid1).metrics_series("steps")
        assert not HealthRepo(conn, uid2).metrics_series("steps")
