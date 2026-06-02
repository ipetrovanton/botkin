"""Сборка структурированного справочника лекарств из официальной выгрузки ГРЛС.

Источник: ZIP-выгрузка ГРЛС (Государственный реестр лекарственных средств РФ), 8 листов-статусов:
Действующий, Изменённый, Исключённый, Истёкший, Выдано по правилам ЕАЭС, Действует (на
подтверждении гос. регистрации), Приостановлено применение, Действует (в иностранных упаковках).

В каждом XLSX: шапка в строке 5 (1-based), данные с строки 7. Значимые колонки (0-based):
  2 — номер регистрационного удостоверения; 8 — торговое наименование;
  9 — МНН (или химическое; "~" = отсутствует).

Результат — `registry.jsonl`: по одной записи на уникальное нормализованное имя
(торговое и/или МНН), с агрегированными статусами (из каких списков), рег-номерами и
связанным МНН (для торговых). Это позволяет в рантайме:
  * фаззи-коррекцию названия по полному списку имён;
  * заполнение МНН из распознанного торгового названия;
  * подсветку статуса (например, препарат в списке «исключён»/«приостановлен»).

Запуск (нужен файл выгрузки, сеть НЕ требуется):
    uv run python -m scripts.build_drug_reference \\
        --src grls2026-06-02-1.zip --out src/botkin/reference/drugs/registry.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Iterator

import openpyxl

# Колонки в XLSX-листах ГРЛС (0-based).
_COL_REG = 2
_COL_TRADE = 8
_COL_MNN = 9
# Данные начинаются после шапки (строки 1-7 — заголовок/шапка/метка статуса).
_DATA_START_ROW = 6  # 0-based индекс первой строки данных

_MIN_NAME_LEN = 3
_HAS_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_NO_MNN_MARKERS = {"~", "", "-"}

# Имя листа (ws.title) → короткий код статуса.
_STATUS_BY_TITLE_PREFIX: dict[str, str] = {
    "Действующий": "active",
    "Изменённый": "modified",
    "Исключённый": "excluded",
    "Истёкший": "expired",
    "Выдано по правилам ЕАЭС": "eaeu",
    "Действует, на подтверждении": "pending",
    "Приостановлено применение": "suspended",
    "Действует, в иностранных": "foreign_pkg",
}


def status_of(sheet_title: str) -> str:
    for prefix, code in _STATUS_BY_TITLE_PREFIX.items():
        if sheet_title.startswith(prefix):
            return code
    return sheet_title.strip().lower()


def normalize_key(name: str) -> str:
    """Ключ для дедупликации и матчинга: lower, ё→е, схлопывание пробелов."""
    return " ".join(str(name).strip().lower().replace("ё", "е").split())


def is_meaningful_name(name: str) -> bool:
    cleaned = str(name).strip()
    return len(cleaned) >= _MIN_NAME_LEN and bool(_HAS_CYRILLIC.search(cleaned))


def _iter_sheet_rows(zip_path: Path) -> Iterator[tuple[str, tuple]]:
    """Отдаёт (status_code, row) по всем листам всех XLSX внутри ZIP."""
    with zipfile.ZipFile(zip_path) as archive:
        for entry in archive.infolist():
            if not entry.filename.lower().endswith(".xlsx"):
                continue
            workbook = openpyxl.load_workbook(io.BytesIO(archive.read(entry)), read_only=True)
            try:
                worksheet = workbook.active
                if worksheet is None:
                    continue
                status = status_of(worksheet.title)
                for index, row in enumerate(worksheet.iter_rows(values_only=True)):
                    if index < _DATA_START_ROW:
                        continue
                    yield status, row
            finally:
                workbook.close()


def build_registry(zip_path: Path) -> dict[str, dict]:
    """Парсит ZIP-выгрузку ГРЛС в структуру: нормализованное имя → запись.

    Запись: {name, types:set, mnn, statuses:set, reg_numbers:set}.
    Торговое и МНН-имя индексируются по своим ключам; имя, встречающееся и как торговое,
    и как МНН, получает types = {"trade", "mnn"}.
    """
    registry: dict[str, dict] = {}

    def ensure(key: str, display: str) -> dict:
        record = registry.get(key)
        if record is None:
            record = {"name": display, "types": set(), "mnn": None,
                      "statuses": set(), "reg_numbers": set()}
            registry[key] = record
        return record

    for status, row in _iter_sheet_rows(zip_path):
        reg = (str(row[_COL_REG]).strip() if row[_COL_REG] is not None else "")
        trade = row[_COL_TRADE]
        mnn = row[_COL_MNN]
        if not reg or not trade or not is_meaningful_name(trade):
            continue

        mnn_display = str(mnn).strip() if mnn is not None else ""
        has_mnn = mnn_display not in _NO_MNN_MARKERS and is_meaningful_name(mnn_display)

        trade_record = ensure(normalize_key(trade), str(trade).strip())
        trade_record["types"].add("trade")
        trade_record["statuses"].add(status)
        trade_record["reg_numbers"].add(reg)
        if has_mnn:
            trade_record["mnn"] = mnn_display

        if has_mnn:
            mnn_record = ensure(normalize_key(mnn_display), mnn_display)
            mnn_record["types"].add("mnn")
            mnn_record["statuses"].add(status)

    return registry


def _record_to_json(record: dict) -> dict:
    types = record["types"]
    kind = "both" if types == {"trade", "mnn"} else next(iter(types))
    out: dict = {
        "name": record["name"],
        "type": kind,
        "statuses": sorted(record["statuses"]),
    }
    if record["mnn"]:
        out["mnn"] = record["mnn"]
    # Рег-номера осмысленны для торговых наименований (конкретные регистрации).
    if "trade" in types and record["reg_numbers"]:
        out["reg_numbers"] = sorted(record["reg_numbers"])
    return out


def write_registry(registry: dict[str, dict], out_path: Path, source_note: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": source_note}, ensure_ascii=False) + "\n")
        for key in sorted(registry):
            f.write(json.dumps(_record_to_json(registry[key]), ensure_ascii=False) + "\n")


def main() -> None:  # pragma: no cover — ручной запуск с файлом выгрузки
    parser = argparse.ArgumentParser(description="Сборка справочника лекарств botkin из ГРЛС")
    parser.add_argument("--src", type=Path, required=True, help="ZIP-выгрузка ГРЛС (8 листов)")
    parser.add_argument("--out", type=Path, required=True, help="Путь к registry.jsonl")
    args = parser.parse_args()

    registry = build_registry(args.src)
    trade = sum(1 for r in registry.values() if "trade" in r["types"])
    mnn = sum(1 for r in registry.values() if "mnn" in r["types"])
    write_registry(registry, args.out, source_note=f"ГРЛС {args.src.name}")
    print(f"Записано {len(registry)} уникальных названий (торговых≈{trade}, МНН≈{mnn}) в {args.out}")


if __name__ == "__main__":  # pragma: no cover
    main()
