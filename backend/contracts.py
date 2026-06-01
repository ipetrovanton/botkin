"""Pydantic-контракты между модулями backend / parsing / bot."""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

_MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _parse_ru_date(value: str | datetime | None) -> datetime | None:
    """Парсит русские даты типа '23 марта 2026 г.' или возвращает datetime как есть."""
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace(" г.", "").replace("г.", "")
    parts = value.split()
    if len(parts) == 3 and parts[1] in _MONTHS_RU:
        try:
            day, month_name, year = parts
            return datetime(int(year), _MONTHS_RU[month_name], int(day))
        except (ValueError, KeyError):
            pass
    # Пробуем ISO формат
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    return None

# ============ ENUMS / ALIAS ============

DocType = Literal["analysis", "prescription", "doctor_report", "certificate", "unknown"]
DocStatus = Literal["received", "processing", "extracted", "failed"]

# ============ DOMAIN MODELS ============


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

    @field_validator("taken_at", mode="before")
    @classmethod
    def _validate_taken_at(cls, v):
        return _parse_ru_date(v)


class Prescription(BaseModel):
    """Одно назначение из рецепта/выписки."""
    model_config = ConfigDict(extra="forbid")
    drug_mnn: str = Field(..., min_length=2)
    drug_trade: Optional[str] = None
    dose: Optional[str] = None
    frequency: Optional[str] = None
    duration_days: Optional[int] = Field(default=None, ge=0)
    prescribed_at: Optional[datetime] = None
    doctor_name: Optional[str] = None
    form_107_1u_flag: bool = False

    @field_validator("prescribed_at", mode="before")
    @classmethod
    def _validate_prescribed_at(cls, v):
        return _parse_ru_date(v)


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
        return _parse_ru_date(v)


# ============ PIPELINE ============


class ClassifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_type: DocType
    confidence: float = Field(..., ge=0.0, le=1.0)


# ============ API ============


class UploadResponse(BaseModel):
    document_id: int
    status: DocStatus