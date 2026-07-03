"""Сборка структурного справочника анализов из выгрузки ФСЛИ (JSON).

Источник: «Справочник лабораторных тестов» ФСЛИ (OID 1.2.643.5.1.13.13.11.1080,
портал НСИ Минздрава). JSON-выгрузка — объект {"records": [ {колонка: значение}, ... ]},
ключи записи — машинные имена колонок: LOINC, FULLNAME, ENGLISHNAME, SHORTNAME,
SYNONYMS (через ';' и/или ','), UNIT, GROUP, TESTSTATUS, NMU.

Результат — registry.jsonl: по записи на тест с каноничным именем, краткой/английской формой,
синонимами, LOINC, кодом НМУ, единицей, группой и статусом. Позволяет в рантайме фаззи-коррекцию
названия по полному набору форм и заполнение LOINC/НМУ/ожидаемой единицы.

Запуск (сеть НЕ требуется):
    uv run python -m scripts.build_analyte_reference \\
        --src "справочник_лабораторных_тестов.json" \\
        --out src/botkin/reference/analytes/registry.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STATUS_MAP = {"Актуальный": "active", "Новый": "new", "Устаревший": "deprecated"}
_MIN_NAME_LEN = 2
_SYNONYM_SEP = re.compile(r"[;,]")  # ФСЛИ смешивает разделители синонимов: ';' и ','


def normalize_key(name: str) -> str:
    """Ключ дедупликации/матчинга: lower, ё→е, схлопывание пробелов."""
    return " ".join(str(name).strip().lower().replace("ё", "е").split())


def _clean(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _split_synonyms(value) -> list[str]:
    if value is None:
        return []
    return [s.strip() for s in _SYNONYM_SEP.split(str(value)) if s.strip()]


def row_to_record(row: dict) -> dict | None:
    """Запись ФСЛИ (dict по именам колонок) → запись реестра или None (если нет имени)."""
    full = _clean(row.get("FULLNAME"))
    if not full or len(full) < _MIN_NAME_LEN:
        return None
    loinc = _clean(row.get("LOINC"))
    if loinc == "0":
        loinc = None
    status_raw = _clean(row.get("TESTSTATUS")) or ""
    return {
        "name": full,
        "short": _clean(row.get("SHORTNAME")),
        "english": _clean(row.get("ENGLISHNAME")),
        "synonyms": _split_synonyms(row.get("SYNONYMS")),
        "loinc": loinc,
        "nmu": _clean(row.get("NMU")),
        "unit": _clean(row.get("UNIT")),
        "group": _clean(row.get("GROUP")),
        "specimen": _clean(row.get("SPECIMEN")),
        "status": STATUS_MAP.get(status_raw, status_raw.lower()),
    }


def build_registry(json_path: Path) -> list[dict]:
    """Читает JSON-выгрузку ФСЛИ {"records": [...]} → дедуплицированный реестр.

    Дедуп по нормализованному имени (см. normalize_key) — первый победитель остаётся.
    JSON-выгрузка плоская: ни битого <dimension>, ни строки описаний (засад xlsx) больше нет.
    Скрипт офлайн-разовый — держать ~20k записей в памяти ок.
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    records_raw = data.get("records", data) if isinstance(data, dict) else data

    seen: set[str] = set()
    out: list[dict] = []
    for row in records_raw:
        record = row_to_record(row)
        if record is None:
            continue
        key = normalize_key(record["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


_UNIT_SEP = re.compile(r"[;,]")  # UNIT иногда перечисляет варианты: «мг/л; мг/дл»


def build_analyte_table(json_path: Path) -> list[dict]:
    """Дедуп-таблица по ANALYTE: одна запись на показатель (без биоматериала/метода).

    Группирует выгрузку ФСЛИ по нормализованному ANALYTE. Имя = ANALYTE (чистое, без
    локуса из FULLNAME), синонимы = ∪(SHORTNAME, ENGLISHNAME, SYNONYMS), единицы =
    ∪(UNIT, расщеплённых по ';'/','). Назначение — подсказка для коррекции опечаток в
    названии/единицах при неуверенном распознавании, БЕЗ подмешивания биоматериала.
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    records_raw = data.get("records", data) if isinstance(data, dict) else data

    groups: dict[str, dict] = {}
    order: list[str] = []
    for row in records_raw:
        analyte = _clean(row.get("ANALYTE"))
        if not analyte or len(analyte) < _MIN_NAME_LEN:
            continue
        # Составные имена ФСЛИ («Альбумин; креатинин») — несколько аналитов в одной записи;
        # для карточки это мусор (коверкает имя), а чистые показатели покрывают их отдельно.
        if ";" in analyte:
            continue
        key = normalize_key(analyte)
        if key not in groups:
            groups[key] = {"name": analyte, "_syn": set(), "_units": set(),
                           "group": _clean(row.get("GROUP"))}
            order.append(key)
        g = groups[key]
        for field in ("SHORTNAME", "ENGLISHNAME"):
            v = _clean(row.get(field))
            if v:
                g["_syn"].add(v)
        for syn in _split_synonyms(row.get("SYNONYMS")):
            g["_syn"].add(syn)
        unit = _clean(row.get("UNIT"))
        if unit:
            for part in _UNIT_SEP.split(unit):
                p = part.strip()
                if p:
                    g["_units"].add(p)

    out: list[dict] = []
    for key in order:
        g = groups[key]
        synonyms = sorted(s for s in g["_syn"] if normalize_key(s) != key)
        out.append({
            "name": g["name"],
            "synonyms": synonyms,
            "units": sorted(g["_units"]),
            "group": g["group"],
        })
    return out


def write_registry(records: list[dict], out_path: Path, source_note: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": source_note}, ensure_ascii=False) + "\n")
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:  # pragma: no cover — ручной запуск с файлом выгрузки
    parser = argparse.ArgumentParser(description="Сборка справочника анализов botkin из ФСЛИ")
    parser.add_argument("--src", type=Path, required=True, help="JSON-выгрузка ФСЛИ")
    parser.add_argument("--out", type=Path, required=True, help="Путь к registry.jsonl")
    args = parser.parse_args()

    records = build_analyte_table(args.src)
    write_registry(records, args.out, source_note=f"ФСЛИ {args.src.name} | ANALYTE-таблица ({len(records)})")
    print(f"Записано {len(records)} показателей (дедуп по ANALYTE) в {args.out}")


if __name__ == "__main__":  # pragma: no cover
    main()
