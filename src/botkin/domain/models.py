"""Доменные модели (Pydantic-контракты)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from botkin.normalize.dates import parse_date as _parse_date

# ── Типы ──────────────────────────────────────────────────────────────────────

DocType = Literal["analysis", "doctor_report", "certificate", "unknown"]
DocStatus = Literal["received", "processing", "extracted", "failed"]

DOC_TYPE_LABELS: dict[str, str] = {
    "analysis": "Анализы 🧪",
    "doctor_report": "Заключение врача 👨‍⚕️",
    "certificate": "Справка 📄",
    "unknown": "Документ 📄",
}

# ── Парсинг русских дат ───────────────────────────────────────────────────────


def parse_ru_date(value: str | datetime | None) -> datetime | None:
    """Совместимость: возвращает только datetime (сырое хранит orchestrator)."""
    dt, _ = _parse_date(value)
    return dt


# ── Модели ────────────────────────────────────────────────────────────────────


class LabResult(BaseModel):
    """Один показатель анализа."""
    model_config = ConfigDict(extra="forbid")

    analyte_code: Optional[str] = None
    analyte_name: str
    value_num: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    ref_operator: Optional[str] = None
    ref_text: Optional[str] = None
    taken_at: Optional[datetime] = None
    source_table_cell: Optional[str] = None
    comments: Optional[str] = None
    value_raw: Optional[str] = None
    unit_raw: Optional[str] = None
    taken_at_raw: Optional[str] = None

    @field_validator("taken_at", mode="before")
    @classmethod
    def _validate_taken_at(cls, v):
        return parse_ru_date(v)


class DoctorReport(BaseModel):
    """Заключение врача."""
    model_config = ConfigDict(extra="forbid")

    diagnosis: Optional[str] = None
    recommendations: list[str] = Field(default_factory=list)
    complaints: list[str] = Field(default_factory=list)
    anamnesis: Optional[str] = None
    visit_date: Optional[datetime] = None
    doctor_name: Optional[str] = None
    department: Optional[str] = None
    medications: list[str] = Field(default_factory=list)

    @field_validator("visit_date", mode="before")
    @classmethod
    def _validate_visit_date(cls, v):
        return parse_ru_date(v)


class ClassifyResult(BaseModel):
    """Результат классификации документа."""
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    confidence: float = Field(..., ge=0.0, le=1.0)
    title: Optional[str] = None
    clinic: Optional[str] = None


class UploadResponse(BaseModel):
    """Ответ API на загрузку документа."""
    document_id: int
    status: DocStatus