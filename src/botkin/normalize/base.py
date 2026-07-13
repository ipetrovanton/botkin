"""Общая основа фаззи-нормализаторов поверх справочников registry.jsonl.

И анализы (ФСЛИ), и лекарства (ГРЛС) сверяются одинаково: нормализуем имя, считаем
лимит правок от длины, ищем ближайшего кандидата по дистанции Дамерау-Левенштейна и
отсекаем слабые совпадения по ratio-floor. Различается только индекс ключей и форма
результата — это и вынесено в наследников через хуки.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

from rapidfuzz import distance, fuzz, process


def normalize_name(name: str) -> str:
    """lower, ё→е, схлопывание пробелов — чтобы матчинг не спотыкался на регистре и опечатках."""
    return " ".join(name.strip().lower().replace("ё", "е").split())


def read_registry(path: Path) -> list[dict]:
    """Читает registry.jsonl, пропуская первую строку с метаданными источника (_meta)."""
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


class BaseNormalizer:
    """Скелет сверки имени со справочником. Наследник задаёт индекс и форму результата."""

    def __init__(self, records: Iterable[dict], max_edit_ratio: float, ratio_floor: float):
        self._max_edit_ratio = max_edit_ratio
        self._ratio_floor = ratio_floor
        self._by_key = self._build_index(list(records))
        self._choices: list[str] = list(self._by_key)

    def _build_index(self, records: list[dict]) -> dict[str, dict]:
        """Ключ поиска → запись. По умолчанию один ключ на запись — её имя.

        Первый победитель остаётся за ключом: дубли имён не перетирают друг друга.
        """
        by_key: dict[str, dict] = {}
        for record in records:
            key = normalize_name(record.get("name") or "")
            if key and key not in by_key:
                by_key[key] = record
        return by_key

    def _prepare_query(self, raw_name: str) -> str:
        """Готовит строку к матчингу. Наследник может срезать лишнее (квалификаторы и т.п.)."""
        return normalize_name(raw_name)

    def _short_circuit(self, query: str, raw_name: str):
        """Хук досрочного ответа до фаззи-поиска (например, точное совпадение аббревиатур)."""
        return None

    def _matched(self, raw_name: str, record: dict, dist: int, ratio: float):
        raise NotImplementedError

    def _unverified(self, raw_name: str, dist: int | None = None, ratio: float = 0.0):
        raise NotImplementedError

    def correct(self, raw_name: str):
        query = self._prepare_query(raw_name)
        if not query or not self._choices:
            return self._unverified(raw_name)

        early = self._short_circuit(query, raw_name)
        if early is not None:
            return early

        # Точное совпадение минует фаззи-скан по всему словарю: у него расстояние 0, и
        # extractOne всё равно вернул бы его. Для частого случая «имя как в реестре» —
        # прямой lookup вместо линейного прохода по десяткам тысяч кандидатов.
        exact = self._by_key.get(query)
        if exact is not None:
            return self._matched(raw_name, exact, 0, 100.0)

        # Лимит правок растёт с длиной имени: короткие строки строже, чтобы не снапнуть мусор.
        cap = max(1, math.floor(len(query) * self._max_edit_ratio))
        best = process.extractOne(
            query, self._choices,
            scorer=distance.DamerauLevenshtein.distance,
            score_cutoff=cap,   # для distance-скорера это максимально допустимое расстояние
        )
        if best is None:
            return self._unverified(raw_name)

        matched_key, dist, _ = best
        ratio = fuzz.ratio(query, matched_key)
        if ratio < self._ratio_floor:
            return self._unverified(raw_name, dist=int(dist), ratio=ratio)
        return self._matched(raw_name, self._by_key[matched_key], int(dist), ratio)
