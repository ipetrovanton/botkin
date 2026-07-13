"""API веб-кабинета: аналитика — селекторы, динамика показателя, сводки за период."""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_user_id
from botkin.db.connection import get_conn
from botkin.db.repos import DocumentRepo, LabRepo, ReportRepo

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytes")
def analytes(user_id: int = Depends(get_user_id)) -> list[str]:
    """Уникальные названия показателей — для селектора динамики."""
    with get_conn() as conn:
        return LabRepo(conn, user_id).distinct_analytes()


@router.get("/clinics")
def clinics(user_id: int = Depends(get_user_id)) -> list[str]:
    """Уникальные клиники — для селектора фильтра."""
    with get_conn() as conn:
        return DocumentRepo(conn, user_id).distinct_clinics()


@router.get("/doctors")
def doctors(user_id: int = Depends(get_user_id)) -> list[dict]:
    """Уникальные врачи с отделением — для селектора фильтра."""
    with get_conn() as conn:
        return ReportRepo(conn, user_id).distinct_doctors()


@router.get("/dynamics")
def dynamics(
    name: str = Query(..., min_length=1, description="Название показателя"),
    user_id: int = Depends(get_user_id),
) -> dict:
    """Серия значений показателя по времени + референсный коридор для графика."""
    with get_conn() as conn:
        points = LabRepo(conn, user_id).dynamics(name, limit=60)
    if not points:
        raise HTTPException(status_code=404, detail=f"Нет данных по «{name}»")
    unit = points[-1].get("unit") or ""
    return {
        "analyte": name,
        "unit": unit,
        "ref_low": points[-1].get("ref_low"),
        "ref_high": points[-1].get("ref_high"),
        "points": [
            {"taken_at": p["taken_at"], "value_num": p["value_num"]} for p in points
        ],
    }


@router.get("/labs/period")
def labs_period(
    user_id: int = Depends(get_user_id),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
) -> list[dict]:
    """Показатели за период, сгруппированные по analyte_name, точки по времени."""
    with get_conn() as conn:
        return LabRepo(conn, user_id).in_period(date_from, date_to)


@router.get("/reports")
def reports(
    user_id: int = Depends(get_user_id),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    doctor: str | None = Query(None),
    clinic: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Заключения врача за период с фильтрами по врачу/клинике."""
    with get_conn() as conn:
        items, total = ReportRepo(conn, user_id).for_period(
            date_from=date_from, date_to=date_to, doctor=doctor, clinic=clinic,
            limit=limit, offset=offset,
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
def stats(user_id: int = Depends(get_user_id)) -> dict:
    """Сводка для дашборда: счётчики документов + границы дат."""
    with get_conn() as conn:
        doc_stats = DocumentRepo(conn, user_id).stats()
        lo, hi = DocumentRepo(conn, user_id).date_range()
        labs = conn.execute(
            "SELECT COUNT(*) AS c FROM lab_results WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        reports = conn.execute(
            "SELECT COUNT(*) AS c FROM doctor_reports WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
    return {**doc_stats, "labs": labs, "reports": reports, "date_min": lo, "date_max": hi}
