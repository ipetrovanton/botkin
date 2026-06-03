"""Фаззи-коррекция названий анализов по справочнику ФСЛИ (registry.jsonl).

По образцу normalize/drugs.py: scorer — абсолютная дистанция Дамерау-Левенштейна
(устойчива к OCR-ошибкам), плюс ratio-floor. Несовпавшее имя НЕ подменяется (status='unverified').

Каждая запись разворачивается в несколько поисковых ключей (полное/краткое/английское имя,
синонимы) → одна каноничная запись. Короткие ключи (аббревиатуры ≤3 символов) требуют точного
совпадения, иначе фаззи на 2-3 символах даёт мусор.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rapidfuzz import distance, fuzz, process

from botkin.config import ANALYTE_MAX_EDIT_RATIO, ANALYTE_RATIO_FLOOR

_REGISTRY_PATH = Path(__file__).parent.parent / "reference" / "analytes" / "registry.jsonl"
_SHORT_KEY_LEN = 3  # ключи такой длины и короче требуют точного совпадения


@dataclass(frozen=True)
class AnalyteMatch:
    raw: str
    canonical: str | None
    loinc: str | None
    nmu: str | None
    group: str | None
    expected_unit: str | None
    status: str            # "matched" | "unverified"
    match_status: str | None   # статус теста в реестре: active | new | deprecated
    distance: int | None
    ratio: float


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().replace("ё", "е").split())


def _unverified(raw: str, dist: int | None = None, ratio: float = 0.0) -> AnalyteMatch:
    return AnalyteMatch(raw=raw, canonical=None, loinc=None, nmu=None, group=None,
                        expected_unit=None, status="unverified", match_status=None,
                        distance=dist, ratio=ratio)


class AnalyteNormalizer:
    """Сверяет распознанные названия анализов со справочником ФСЛИ через RapidFuzz."""

    def __init__(
        self,
        records: Iterable[dict],
        max_edit_ratio: float = ANALYTE_MAX_EDIT_RATIO,
        ratio_floor: float = ANALYTE_RATIO_FLOOR,
    ):
        self._max_edit_ratio = max_edit_ratio
        self._ratio_floor = ratio_floor
        # Поисковый ключ → каноничная запись. Первый победитель остаётся.
        self._by_key: dict[str, dict] = {}
        for record in records:
            forms = [record.get("name"), record.get("short"), record.get("english")]
            forms.extend(record.get("synonyms", []))
            for form in forms:
                if not form:
                    continue
                key = _normalize_name(form)
                if key and key not in self._by_key:
                    self._by_key[key] = record
        self._choices: list[str] = list(self._by_key)

    def _result(self, raw_name: str, record: dict, dist: int, ratio: float) -> AnalyteMatch:
        return AnalyteMatch(
            raw=raw_name,
            canonical=record["name"],
            loinc=record.get("loinc"),
            nmu=record.get("nmu"),
            group=record.get("group"),
            expected_unit=record.get("unit"),
            status="matched",
            match_status=record.get("status"),
            distance=dist,
            ratio=ratio,
        )

    def correct(self, raw_name: str) -> AnalyteMatch:
        query = _normalize_name(raw_name)
        if not query or not self._choices:
            return _unverified(raw_name)

        # Короткие ключи (аббревиатуры) — только точное совпадение.
        if len(query) <= _SHORT_KEY_LEN:
            record = self._by_key.get(query)
            if record is not None:
                return self._result(raw_name, record, 0, 100.0)
            return _unverified(raw_name)

        cap = max(1, math.floor(len(query) * self._max_edit_ratio))
        best = process.extractOne(
            query, self._choices,
            scorer=distance.DamerauLevenshtein.distance,
            score_cutoff=cap,
        )
        if best is None:
            return _unverified(raw_name)

        matched_key, dist, _ = best
        ratio = fuzz.ratio(query, matched_key)
        if ratio < self._ratio_floor:
            return _unverified(raw_name, dist=int(dist), ratio=ratio)
        return self._result(raw_name, self._by_key[matched_key], int(dist), ratio)


def _read_registry(path: Path = _REGISTRY_PATH) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "_meta" in obj:
            continue
        records.append(obj)
    return records


def load_default() -> AnalyteNormalizer:
    return AnalyteNormalizer(_read_registry())
