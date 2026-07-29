"""Единый слой доступа к данным — фасад для обратной совместимости.

Классы разнесены по домен-модулям (user_repo, document_repo, lab_repo,
report_repo, patient_repo, health_repo). Этот файл сохраняет API для
существующих импортеров: `from botkin.db.repos import DocumentRepo, ...`.
"""
from __future__ import annotations

from .base import BaseRepo
from .document_repo import DocumentRepo
from .health_repo import HealthRepo
from .lab_repo import LabRepo
from .patient_repo import PatientRepo
from .report_repo import ReportRepo
from .user_repo import AuthRepo, UserRepo

__all__ = [
    "BaseRepo",
    "UserRepo",
    "AuthRepo",
    "DocumentRepo",
    "LabRepo",
    "ReportRepo",
    "PatientRepo",
    "HealthRepo",
]
