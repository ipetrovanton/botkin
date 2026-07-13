"""API форм пациента: профиль тела, жалобы, текущие препараты.

Данные учитываются в контексте RAG-рекомендаций (rag/recommend.py) —
модель видит пол/возраст/антропометрию, аллергии, хронические состояния,
актуальные жалобы и принимаемые препараты.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from botkin.db.connection import get_conn
from botkin.db.repos import PatientRepo

from ..deps import get_user_id

router = APIRouter(prefix="/api/patient", tags=["patient"])


class ProfileRequest(BaseModel):
    """Профиль тела; учитываются только явно переданные поля (PATCH-семантика)."""

    sex: str | None = Field(None, pattern="^(male|female)$")
    birth_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    height_cm: float | None = Field(None, gt=30, lt=300)
    weight_kg: float | None = Field(None, gt=1, lt=500)
    blood_type: str | None = Field(None, max_length=20)
    allergies: str | None = Field(None, max_length=2000)
    chronic_conditions: str | None = Field(None, max_length=2000)

    def set_fields(self) -> dict:
        return {k: getattr(self, k) for k in self.model_fields_set}


class ComplaintRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=4000)


class MedicationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    dosage: str | None = Field(None, max_length=200)
    schedule: str | None = Field(None, max_length=300)


@router.get("/profile")
def get_profile(user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        return PatientRepo(conn, user_id).get_profile() or {}


@router.put("/profile")
def put_profile(req: ProfileRequest, user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        return PatientRepo(conn, user_id).upsert_profile(req.set_fields())


@router.get("/complaints")
def list_complaints(user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        return {"items": PatientRepo(conn, user_id).list_complaints()}


@router.post("/complaints", status_code=201)
def add_complaint(req: ComplaintRequest, user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        cid = PatientRepo(conn, user_id).add_complaint(req.text.strip())
    return {"id": cid}


@router.delete("/complaints/{complaint_id}")
def delete_complaint(complaint_id: int, user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        if not PatientRepo(conn, user_id).delete_complaint(complaint_id):
            raise HTTPException(status_code=404, detail="Жалоба не найдена")
    return {"deleted": 1}


@router.get("/medications")
def list_medications(user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        return {"items": PatientRepo(conn, user_id).list_medications()}


@router.post("/medications", status_code=201)
def add_medication(req: MedicationRequest, user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        mid = PatientRepo(conn, user_id).add_medication(
            req.name.strip(), req.dosage, req.schedule,
        )
    return {"id": mid}


@router.patch("/medications/{med_id}")
def toggle_medication(
    med_id: int, is_active: bool, user_id: int = Depends(get_user_id),
) -> dict:
    """Смена статуса приёма: is_active=false — «приём завершён», история остаётся."""
    with get_conn() as conn:
        if not PatientRepo(conn, user_id).set_medication_active(med_id, is_active):
            raise HTTPException(status_code=404, detail="Препарат не найден")
    return {"id": med_id, "is_active": is_active}


@router.delete("/medications/{med_id}")
def delete_medication(med_id: int, user_id: int = Depends(get_user_id)) -> dict:
    with get_conn() as conn:
        if not PatientRepo(conn, user_id).delete_medication(med_id):
            raise HTTPException(status_code=404, detail="Препарат не найден")
    return {"deleted": 1}
