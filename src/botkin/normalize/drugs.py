"""Фаззи-коррекция названий лекарств по структурному справочнику ГРЛС (registry.jsonl).

Scorer выбран по замеру на словаре 20 948 названий (см. спек): абсолютная дистанция
Дамерау-Левенштейна ставит верный ответ первым (OCR-ошибки = расстояние 1–3), тогда как WRatio
и JaroWinkler на большом словаре дают ложные совпадения.

Правило безопасности: если совпадение не проходит порог (cap по расстоянию ИЛИ ratio-floor),
название НЕ подменяется — оригинал сохраняется, статус 'unverified' (защита редких препаратов).

На matched возвращается запись справочника: каноничное имя, тип, связанный МНН (для торговых —
позволяет заполнить МНН), статусы-списки (для подсветки «исключён»/«приостановлено») и рег-номера.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from botkin.config import DRUG_MAX_EDIT_RATIO, DRUG_RATIO_FLOOR
from botkin.normalize.base import BaseNormalizer, read_registry

_REGISTRY_PATH = Path(__file__).parent.parent / "reference" / "drugs" / "registry.jsonl"
# Хвост свободного текста: всё с первой цифры или разделителя дозы/формы.
_DOSE_TAIL_RE = re.compile(r"[-–—,(]|\d")


@dataclass(frozen=True)
class DrugMatch:
    """Результат сверки названия со справочником."""
    raw: str                          # что прочла модель (всегда сохраняется)
    canonical: str | None             # каноничное название или None
    type: str | None                  # "trade" | "mnn" | "both"
    mnn: str | None                   # связанное МНН (для торговых)
    statuses: tuple[str, ...]         # списки-статусы из реестра
    reg_numbers: tuple[str, ...]      # номера РУ (для торговых)
    status: str                       # "matched" | "unverified"
    distance: int | None              # расстояние Дамерау-Левенштейна
    ratio: float                      # fuzz.ratio к кандидату (0–100)


class DrugNormalizer(BaseNormalizer):
    """Сверяет распознанные названия лекарств со структурным справочником через RapidFuzz."""

    def __init__(
        self,
        records: Iterable[dict],
        max_edit_ratio: float = DRUG_MAX_EDIT_RATIO,
        ratio_floor: float = DRUG_RATIO_FLOOR,
    ):
        super().__init__(records, max_edit_ratio, ratio_floor)

    def _matched(self, raw_name: str, record: dict, dist: int, ratio: float) -> DrugMatch:
        return DrugMatch(
            raw=raw_name,
            canonical=record["name"],
            type=record.get("type"),
            mnn=record.get("mnn"),
            statuses=tuple(record.get("statuses", ())),
            reg_numbers=tuple(record.get("reg_numbers", ())),
            status="matched",
            distance=dist,
            ratio=ratio,
        )

    def _unverified(self, raw_name: str, dist: int | None = None, ratio: float = 0.0) -> DrugMatch:
        return DrugMatch(raw=raw_name, canonical=None, type=None, mnn=None, statuses=(),
                         reg_numbers=(), status="unverified", distance=dist, ratio=ratio)

    def correct_free_text(self, line: str) -> DrugMatch:
        """Best-effort для строк с дозой/формой (doctor_report.medications).

        Отрезает хвост с первой цифры/разделителя, берёт ведущее имя, при неудаче — первое слово.
        Оригинальная строка сохраняется как raw.
        """
        head = _DOSE_TAIL_RE.split(line, maxsplit=1)[0].strip()
        if not head:
            return self._unverified(line)
        match = self.correct(head)
        if match.status == "unverified" and " " in head:
            match = self.correct(head.split()[0])
        # raw всегда = исходная строка целиком
        return DrugMatch(
            raw=line, canonical=match.canonical, type=match.type, mnn=match.mnn,
            statuses=match.statuses, reg_numbers=match.reg_numbers,
            status=match.status, distance=match.distance, ratio=match.ratio,
        )


@lru_cache(maxsize=1)
def load_default() -> DrugNormalizer:
    """Нормализатор из упакованного registry.jsonl. Кэшируется: словарь ГРЛС читается раз."""
    return DrugNormalizer(read_registry(_REGISTRY_PATH))
