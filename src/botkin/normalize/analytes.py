"""Фаззи-коррекция названий анализов по справочнику ФСЛИ (registry.jsonl).

По образцу normalize/drugs.py: scorer — абсолютная дистанция Дамерау-Левенштейна
(устойчива к OCR-ошибкам), плюс ratio-floor. Несовпавшее имя НЕ подменяется (status='unverified').

Каждая запись разворачивается в несколько поисковых ключей (полное/краткое/английское имя,
синонимы) → одна каноничная запись. Короткие ключи (аббревиатуры ≤3 символов) требуют точного
совпадения, иначе фаззи на 2-3 символах даёт мусор.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from botkin.config import ANALYTE_MAX_EDIT_RATIO, ANALYTE_RATIO_FLOOR
from botkin.normalize.base import BaseNormalizer, normalize_name, read_registry

_REGISTRY_PATH = Path(__file__).parent.parent / "reference" / "analytes" / "registry.jsonl"
_SHORT_KEY_LEN = 3  # ключи такой длины и короче требуют точного совпадения


@dataclass(frozen=True)
class AnalyteMatch:
    raw: str
    canonical: str | None        # чистое имя показателя (ANALYTE), без биоматериала/метода
    loinc: str | None
    nmu: str | None
    group: str | None
    expected_units: tuple[str, ...]   # известные единицы показателя (подсказка/проверка)
    status: str            # "matched" | "unverified"
    match_status: str | None
    distance: int | None
    ratio: float


# ── Распознавание панели «Общий анализ крови» по составу показателей ──────────
# Группа ФСЛИ — это раздел номенклатуры, а не тип бланка: Гемоглобин/Эритроциты/
# Лейкоциты в реестре отнесены к «Химико-микроскопическим исследованиям» (определяются
# и в моче/кале), поэтому голосование по группам даёт мусорный заголовок для ОАК.
# Клиническую группу однозначно задаёт СОСТАВ панели, а не отдельный показатель.

CBC_TITLE = "Общий анализ крови"
HEMATOLOGY_GROUP = "Гематологические исследования"

# Ядро ОАК — присутствия нескольких этих показателей достаточно, чтобы опознать панель.
_CBC_CORE = frozenset({"гемоглобин", "эритроциты", "лейкоциты", "тромбоциты", "гематокрит"})
# Полный набор показателей ОАК (для проставления клинической группы каждой строке).
_CBC_ALL = _CBC_CORE | frozenset({
    "mcv", "mch", "mchc", "rdw", "mpv", "pdw", "pct", "тромбокрит",
    "нейтрофилы", "лимфоциты", "моноциты", "эозинофилы", "базофилы",
    "соэ", "ретикулоциты",
})


def _clean_analyte_name(name: str) -> str:
    """Нормализует имя показателя и снимает квалификаторы («MCHC (…)» → «mchc»)."""
    return _strip_qualifiers(normalize_name(name))


def is_cbc_analyte(name: str | None) -> bool:
    """Принадлежит ли показатель набору общего анализа крови."""
    return bool(name) and _clean_analyte_name(name) in _CBC_ALL


def is_cbc_panel(names: Iterable[str | None]) -> bool:
    """Опознаёт панель ОАК по составу: достаточно ядра гематологических показателей."""
    cleaned = {_clean_analyte_name(n) for n in names if n}
    return len(cleaned & _CBC_CORE) >= 3


# ── Обобщённый заголовок документа по группе исследований ────────────────────
# Заголовок «С-реактивный белок» (по одному показателю) неинформативен. Обобщаем
# по преобладающей группе ФСЛИ нормализованных показателей (биоматериал из справочника
# намеренно не используем — он подмешивал ложный локус). Гематология/биохимия и т.п.

def summary_title(
    groups: Iterable[str | None],
    test_names: Iterable[str | None] = (),
    fallback: str = "Лабораторные анализы",
) -> str:
    """Заголовок документа: панель ОАК → преобладающая группа → название → fallback."""
    from collections import Counter

    names = [t.strip() for t in test_names if t and t.strip()]
    # Состав панели сильнее голосования по группам ФСЛИ (см. комментарий выше).
    if is_cbc_panel(names):
        return CBC_TITLE
    cats = [g.strip() for g in groups if g and g.strip()]
    if cats:
        return Counter(cats).most_common(1)[0][0]
    if names:
        return Counter(names).most_common(1)[0][0]
    return fallback


# Квалификаторы, которые модель/бланк дописывают к названию показателя и которые сбивают
# матч: скобочные пояснения «(общ.число)» и хвосты «, %» / «, абс.» / «, отн.».
_PARENS_RE = re.compile(r"\([^)]*\)")
_TAIL_QUALIFIER_RE = re.compile(r"[\s,]+(?:%|абс\.?|отн\.?)\s*$")


def _strip_qualifiers(query: str) -> str:
    """Убирает скобочные пояснения и хвостовые «, %»/«, абс.» из нормализованного имени."""
    s = _PARENS_RE.sub(" ", query)
    prev = None
    while prev != s:                       # хвостов может быть несколько: «…, абс., %»
        prev = s
        s = _TAIL_QUALIFIER_RE.sub("", s)
    return " ".join(s.split()).strip(" ,")


class AnalyteNormalizer(BaseNormalizer):
    """Сверяет распознанные названия анализов со справочником ФСЛИ через RapidFuzz."""

    def __init__(
        self,
        records: Iterable[dict],
        max_edit_ratio: float = ANALYTE_MAX_EDIT_RATIO,
        ratio_floor: float = ANALYTE_RATIO_FLOOR,
    ):
        super().__init__(records, max_edit_ratio, ratio_floor)

    def _build_index(self, records: list[dict]) -> dict[str, dict]:
        # Канонические имена имеют приоритет над синонимами: иначе чужой синоним «крадёт»
        # ключ (например «тромбоциты» как синоним CD31+клетки перебивал показатель
        # «Тромбоциты»). Два прохода: сначала имена, затем синонимы на свободные ключи.
        by_key: dict[str, dict] = {}
        for record in records:
            key = normalize_name(record.get("name") or "")
            if key and key not in by_key:
                by_key[key] = record
        for record in records:
            for syn in record.get("synonyms", []):
                key = normalize_name(syn or "")
                if key and key not in by_key:
                    by_key[key] = record
        return by_key

    def _prepare_query(self, raw_name: str) -> str:
        query = normalize_name(raw_name)
        # Отсекаем квалификаторы («, %», «, абс.», «(...)») — модель часто их дописывает.
        # Но если в остатке лишь аббревиатура (≤3), откатываемся к оригиналу: голый «mcv»
        # точно совпал бы со случайным синонимом, а полная строка честно уйдёт в unverified.
        stripped = _strip_qualifiers(query)
        return stripped if len(stripped) > _SHORT_KEY_LEN else query

    def _short_circuit(self, query: str, raw_name: str) -> AnalyteMatch | None:
        # Короткие ключи (аббревиатуры) — только точное совпадение, фаззи на них даёт мусор.
        if len(query) <= _SHORT_KEY_LEN:
            record = self._by_key.get(query)
            if record is not None:
                return self._matched(raw_name, record, 0, 100.0)
            return self._unverified(raw_name)
        return None

    def _matched(self, raw_name: str, record: dict, dist: int, ratio: float) -> AnalyteMatch:
        return AnalyteMatch(
            raw=raw_name,
            canonical=record["name"],   # ANALYTE — чистое имя без биоматериала
            loinc=record.get("loinc"),
            nmu=record.get("nmu"),
            group=record.get("group"),
            expected_units=tuple(record.get("units", [])),
            status="matched",
            match_status=record.get("status"),
            distance=dist,
            ratio=ratio,
        )

    def _unverified(self, raw_name: str, dist: int | None = None, ratio: float = 0.0) -> AnalyteMatch:
        return AnalyteMatch(raw=raw_name, canonical=None, loinc=None, nmu=None, group=None,
                            expected_units=(), status="unverified", match_status=None,
                            distance=dist, ratio=ratio)


@lru_cache(maxsize=1)
def load_default() -> AnalyteNormalizer:
    """Нормализатор из упакованного registry.jsonl. Кэшируется: реестр ФСЛИ читается раз."""
    return AnalyteNormalizer(read_registry(_REGISTRY_PATH))
