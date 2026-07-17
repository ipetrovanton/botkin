"""Тесты верификации распознанного: CRUD показателей документа, правка заключений,
подтверждение и сброс verified_at при любой правке."""
import importlib

import botkin.config
import botkin.db.connection

TG = "555"


def _client(monkeypatch, tmp_path):
    db = tmp_path / "verify.db"
    monkeypatch.setenv("SQLITE_PATH", str(db))
    importlib.reload(botkin.config)
    importlib.reload(botkin.db.connection)
    botkin.db.connection.init_db()

    import botkin.llm.client as llm_client
    monkeypatch.setattr(llm_client, "warmup", lambda: None)

    from fastapi.testclient import TestClient
    import botkin.api.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def _hdr(tg: str = TG) -> dict:
    return {"X-Telegram-User-Id": tg}


def _seed_analysis(tg: str = TG) -> tuple[int, int]:
    """Документ-анализ с одним показателем; возвращает (document_id, lab_id)."""
    from botkin.db.repos import DocumentRepo, LabRepo, UserRepo

    with botkin.db.connection.get_conn() as conn:
        uid = UserRepo(conn).get_or_create(int(tg))
        repo = DocumentRepo(conn, uid)
        d = repo.create(source_path="/tmp/v.pdf")
        repo.set_doc_type(d, "analysis")
        repo.set_status(d, "extracted")
        lab_id = LabRepo(conn, uid).insert_manual(d, {
            "analyte_name": "Гемоглобин", "value_num": 140.0, "unit": "г/л",
            "ref_low": 120.0, "ref_high": 160.0, "taken_at": "2026-07-01",
        })
    return d, lab_id


def _seed_report(tg: str = TG) -> tuple[int, int]:
    """Документ-заключение; возвращает (document_id, report_id)."""
    from botkin.db.repos import DocumentRepo, ReportRepo, UserRepo

    with botkin.db.connection.get_conn() as conn:
        uid = UserRepo(conn).get_or_create(int(tg))
        repo = DocumentRepo(conn, uid)
        d = repo.create(source_path="/tmp/r.jpg")
        repo.set_doc_type(d, "doctor_report")
        repo.set_status(d, "extracted")
        ReportRepo(conn, uid).save([{
            "document_id": d, "user_id": uid, "diagnosis": "ОРВИ",
            "recommendations_json": '["Покой"]', "complaints_json": "[]",
            "anamnesis": None, "medications_json": '["Парацетамол"]',
            "medications_normalized_json": None,
            "visit_date": "2026-07-01", "doctor_name": "Иванов И.И.",
            "department": "Терапия",
        }])
        report_id = conn.execute(
            "SELECT id FROM doctor_reports WHERE document_id = ?", (d,)
        ).fetchone()["id"]
    return d, report_id


def test_verify_and_reset_on_edit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    d, lab_id = _seed_analysis()
    # подтверждение
    r = client.post(f"/api/documents/{d}/verify", headers=_hdr())
    assert r.status_code == 200 and r.json()["verified_at"]
    doc = client.get(f"/api/documents/{d}", headers=_hdr()).json()["document"]
    assert doc["verified_at"]
    # правка сбрасывает отметку
    r2 = client.patch(f"/api/documents/{d}/labs/{lab_id}",
                      json={"value_num": 150.0}, headers=_hdr())
    assert r2.status_code == 200 and r2.json()["value_num"] == 150.0
    doc = client.get(f"/api/documents/{d}", headers=_hdr()).json()["document"]
    assert doc["verified_at"] is None


def test_lab_edit_partial_does_not_wipe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    d, lab_id = _seed_analysis()
    r = client.patch(f"/api/documents/{d}/labs/{lab_id}",
                     json={"unit": "g/L"}, headers=_hdr())
    body = r.json()
    assert body["unit"] == "g/L"
    assert body["analyte_name"] == "Гемоглобин"
    assert body["value_num"] == 140.0


def test_lab_add_and_delete(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    d, _ = _seed_analysis()
    r = client.post(f"/api/documents/{d}/labs",
                    json={"analyte_name": "СОЭ", "value_num": 8.0, "unit": "мм/ч"},
                    headers=_hdr())
    assert r.status_code == 201
    new_id = r.json()["id"]
    assert r.json()["match_status"] == "manual"
    labs = client.get(f"/api/documents/{d}", headers=_hdr()).json()["labs"]
    assert len(labs) == 2
    r2 = client.delete(f"/api/documents/{d}/labs/{new_id}", headers=_hdr())
    assert r2.status_code == 200
    labs = client.get(f"/api/documents/{d}", headers=_hdr()).json()["labs"]
    assert len(labs) == 1


def test_lab_of_foreign_document_404(monkeypatch, tmp_path):
    """Показатель одного документа нельзя править через id другого документа."""
    client = _client(monkeypatch, tmp_path)
    d1, lab_id = _seed_analysis()
    d2, _ = _seed_report()
    r = client.patch(f"/api/documents/{d2}/labs/{lab_id}",
                     json={"value_num": 1.0}, headers=_hdr())
    assert r.status_code == 404


def test_foreign_user_cannot_edit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    d, lab_id = _seed_analysis()
    r = client.patch(f"/api/documents/{d}/labs/{lab_id}",
                     json={"value_num": 1.0}, headers=_hdr("999"))
    assert r.status_code == 404


def test_report_edit_lists_serialized(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    d, report_id = _seed_report()
    r = client.patch(
        f"/api/documents/{d}/reports/{report_id}",
        json={"diagnosis": "Грипп", "recommendations": ["Покой", "Обильное питьё"]},
        headers=_hdr(),
    )
    assert r.status_code == 200
    detail = client.get(f"/api/documents/{d}", headers=_hdr()).json()
    rep = detail["reports"][0]
    assert rep["diagnosis"] == "Грипп"
    assert rep["recommendations"] == ["Покой", "Обильное питьё"]
    assert rep["medications"] == ["Парацетамол"]  # не переданное поле не тронуто
