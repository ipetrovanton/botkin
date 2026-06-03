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

    records = build_registry(args.src)
    write_registry(records, args.out, source_note=f"ФСЛИ {args.src.name} ({len(records)})")
    print(f"Записано {len(records)} тестов в {args.out}")


if __name__ == "__main__":  # pragma: no cover
    main()
