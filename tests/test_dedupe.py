"""Дедупликация повторных загрузок одного документа.

Пользователь может загрузить тот же документ несколько раз (переслал ещё раз,
пере-сканировал, обновлённая версия из клиники). Храним ОДИН документ с
максимально достоверными данными:
- количество показателей выросло или не изменилось → побеждают НОВЫЕ данные
  (свежий прогон достовернее), старый документ удаляется целиком;
- количество уменьшилось → остаются СТАРЫЕ (новый прогон потерял строки),
  новый документ удаляется.

Дубликат распознаётся по sha256 файла (точный повтор) либо по совпадению
(doc_type, title, clinic) — пере-скан того же бланка.
"""
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, UserRepo
from botkin.pipeline.orchestrator import dedupe_document


def _lab_row(doc_id: int, uid: int, name: str, value: float) -> dict:
    return {
        "document_id": doc_id, "user_id": uid, "analyte_code": None,
        "analyte_name": name, "value_num": value, "value_text": None,
        "unit": "г/л", "ref_low": 120.0, "ref_high": 160.0,
        "ref_operator": None, "ref_text": None, "taken_at": "2026-06-05",
        "source_table_cell": None, "value_raw": str(value), "unit_raw": None,
        "taken_at_raw": None, "analyte_canonical": name, "loinc": None,
        "nmu_code": None, "analyte_group": None, "match_status": "matched",
        "unit_expected": "г/л", "unit_mismatch": 0,
    }


def _make_doc(conn, uid, *, sha, labs, title="ОАК", clinic="Инвитро", path="/tmp/x.pdf"):
    repo = DocumentRepo(conn, uid)
    did = repo.create(source_path=path, file_sha256=sha)
    repo.set_doc_type(did, "analysis")
    repo.set_metadata(did, title=title, clinic=clinic)
    repo.set_status(did, "extracted")
    LabRepo(conn, uid).save_results(
        [_lab_row(did, uid, f"Показатель {i}", 100.0 + v) for i, v in enumerate(labs)]
    )
    return did


def test_same_count_new_values_win(set_test_db):
    """Тот же файл, то же количество строк, значения другие → остаются новые."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        old = _make_doc(conn, uid, sha="abc", labs=[1.0, 2.0])
        new = _make_doc(conn, uid, sha="abc", labs=[5.0, 6.0])

        survived = dedupe_document(conn, new, uid)

        repo = DocumentRepo(conn, uid)
        assert survived is True
        assert repo.get(old) is None          # старый удалён целиком
        assert repo.get(new) is not None
        values = [r["value_num"] for r in LabRepo(conn, uid).for_document(new)]
    assert values == [105.0, 106.0]           # новые значения


def test_fewer_rows_old_wins(set_test_db):
    """Новый прогон потерял строки → старые данные достовернее, новый удаляется."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        old = _make_doc(conn, uid, sha="abc", labs=[1.0, 2.0, 3.0])
        new = _make_doc(conn, uid, sha="abc", labs=[9.0])

        survived = dedupe_document(conn, new, uid)

        repo = DocumentRepo(conn, uid)
        assert survived is False
        assert repo.get(new) is None
        assert repo.get(old) is not None
        values = [r["value_num"] for r in LabRepo(conn, uid).for_document(old)]
    assert values == [101.0, 102.0, 103.0]     # старые значения целы


def test_more_rows_new_wins(set_test_db):
    """Новый прогон полнее (добрал строки) → новые данные побеждают."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        old = _make_doc(conn, uid, sha="abc", labs=[1.0])
        new = _make_doc(conn, uid, sha="abc", labs=[5.0, 6.0, 7.0])

        survived = dedupe_document(conn, new, uid)

        repo = DocumentRepo(conn, uid)
        assert survived is True
        assert repo.get(old) is None
        assert len(LabRepo(conn, uid).for_document(new)) == 3


def test_semantic_duplicate_without_sha(set_test_db):
    """Пере-скан того же бланка (другой файл): дубль по (doc_type, title, clinic)."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        old = _make_doc(conn, uid, sha="aaa", labs=[1.0, 2.0], path="/tmp/scan1.pdf")
        new = _make_doc(conn, uid, sha="bbb", labs=[5.0, 6.0], path="/tmp/scan2.pdf")

        survived = dedupe_document(conn, new, uid)

        repo = DocumentRepo(conn, uid)
        assert survived is True
        assert repo.get(old) is None and repo.get(new) is not None


def test_different_documents_not_deduped(set_test_db):
    """Разные документы (другой title) — не дубль, оба живут."""
    with get_conn() as conn:
        uid = UserRepo(conn).get_or_create(42)
        a = _make_doc(conn, uid, sha="aaa", labs=[1.0], title="ОАК")
        b = _make_doc(conn, uid, sha="bbb", labs=[2.0], title="Биохимия")

        survived = dedupe_document(conn, b, uid)

        repo = DocumentRepo(conn, uid)
        assert survived is True
        assert repo.get(a) is not None and repo.get(b) is not None


def test_dedupe_scoped_to_user(set_test_db):
    """Одинаковый документ у РАЗНЫХ пользователей — не дубль (tenant-изоляция)."""
    with get_conn() as conn:
        uid_a = UserRepo(conn).get_or_create(42)
        uid_b = UserRepo(conn).get_or_create(99)
        doc_a = _make_doc(conn, uid_a, sha="abc", labs=[1.0])
        doc_b = _make_doc(conn, uid_b, sha="abc", labs=[2.0])

        survived = dedupe_document(conn, doc_b, uid_b)

        assert survived is True
        assert DocumentRepo(conn, uid_a).get(doc_a) is not None
        assert DocumentRepo(conn, uid_b).get(doc_b) is not None
