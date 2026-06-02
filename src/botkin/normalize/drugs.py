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

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rapidfuzz import distance, fuzz, process

from botkin.config import DRUG_MAX_EDIT_RATIO, DRUG_RATIO_FLOOR

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


def _normalize_name(name: str) -> str:
    """lower, ё→е, схлопывание пробелов — для устойчивого матчинга."""
    return " ".join(name.strip().lower().replace("ё", "е").split())


def _unverified(raw: str, dist: int | None = None, ratio: float = 0.0) -> DrugMatch:
    return DrugMatch(raw=raw, canonical=None, type=None, mnn=None, statuses=(),
                     reg_numbers=(), status="unverified", distance=dist, ratio=ratio)


class DrugNormalizer:
    """Сверяет распознанные названия лекарств со структурным справочником через RapidFuzz."""

    def __init__(
        self,
        records: Iterable[dict],
        max_edit_ratio: float = DRUG_MAX_EDIT_RATIO,
        ratio_floor: float = DRUG_RATIO_FLOOR,
    ):
        self._max_edit_ratio = max_edit_ratio
        self._ratio_floor = ratio_floor
        # Карта: нормализованное имя → запись справочника.
        self._by_key: dict[str, dict] = {}
        for record in records:
            key = _normalize_name(record["name"])
            if key and key not in self._by_key:
                self._by_key[key] = record
        self._choices: list[str] = list(self._by_key)

    def correct(self, raw_name: str) -> DrugMatch:
        query = _normalize_name(raw_name)
        if not query or not self._choices:
            return _unverified(raw_name)

        # Лимит правок зависит от длины: короткие имена строже (меньше ложных снапов).
        cap = max(1, math.floor(len(query) * self._max_edit_ratio))
        best = process.extractOne(
            query, self._choices,
            scorer=distance.DamerauLevenshtein.distance,
            score_cutoff=cap,   # для distance-scorer это МАКСимально допустимое расстояние
        )
        if best is None:
            return _unverified(raw_name)

        matched_key, dist, _ = best
        ratio = fuzz.ratio(query, matched_key)
        if ratio < self._ratio_floor:
            return _unverified(raw_name, dist=int(dist), ratio=ratio)

        record = self._by_key[matched_key]
        return DrugMatch(
            raw=raw_name,
            canonical=record["name"],
            type=record.get("type"),
            mnn=record.get("mnn"),
            statuses=tuple(record.get("statuses", ())),
            reg_numbers=tuple(record.get("reg_numbers", ())),
            status="matched",
            distance=int(dist),
            ratio=ratio,
        )

    def correct_free_text(self, line: str) -> DrugMatch:
        """Best-effort для строк с дозой/формой (doctor_report.medications).

        Отрезает хвост с первой цифры/разделителя, берёт ведущее имя, при неудаче — первое слово.
        Оригинальная строка сохраняется как raw.
        """
        head = _DOSE_TAIL_RE.split(line, maxsplit=1)[0].strip()
        if not head:
            return _unverified(line)
        match = self.correct(head)
        if match.status == "unverified" and " " in head:
            match = self.correct(head.split()[0])
        # raw всегда = исходная строка целиком
        return DrugMatch(
            raw=line, canonical=match.canonical, type=match.type, mnn=match.mnn,
            statuses=match.statuses, reg_numbers=match.reg_numbers,
            status=match.status, distance=match.distance, ratio=match.ratio,
        )


def _read_registry(path: Path = _REGISTRY_PATH) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "_meta" in obj:   # первая строка — метаданные источника
            continue
        records.append(obj)
    return records


def load_default() -> DrugNormalizer:
    """Создаёт нормализатор из упакованного registry.jsonl и параметров из config."""
    return DrugNormalizer(_read_registry())
