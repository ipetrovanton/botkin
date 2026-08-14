from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from fact_package import build_fact_package


def _date(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _patient_key(name: str, birth_date: str | None) -> str:
    return f"{name}|{birth_date or 'unknown'}"


def group_documents(records: Iterable[dict]) -> dict[str, list[dict]]:
    items = list(records)
    births: dict[str, set[str]] = defaultdict(set)
    for item in items:
        if item.get("patient_name") and item.get("patient_birth_date"):
            births[item["patient_name"]].add(item["patient_birth_date"])
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        name = item.get("patient_name")
        if not name:
            raise ValueError(f"patient_name отсутствует: {item.get('filename')}")
        birth = item.get("patient_birth_date")
        candidates = births.get(name, set())
        if birth is None and len(candidates) == 1:
            birth = next(iter(candidates))
        groups[_patient_key(name, birth)].append(item)
    return dict(groups)


def _records_for_package(records: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    labs, reports, missing_dates = [], [], []
    for document in records:
        filename = document["filename"]
        visit_date = _date(document.get("visit_date"))
        if document.get("doc_type") == "analysis":
            if visit_date is None:
                missing_dates.append(filename)
            for index, analyte in enumerate(document.get("analytes") or []):
                labs.append({
                    "id": index,
                    "document_id": filename,
                    "taken_at": visit_date,
                    "name": analyte.get("name"),
                    "raw_name": analyte.get("name"),
                    "value_num": analyte.get("value"),
                    "value_text": analyte.get("value_text"),
                    "unit": analyte.get("unit"),
                    "unit_raw": analyte.get("unit"),
                    "ref_low": analyte.get("ref_low"),
                    "ref_high": analyte.get("ref_high"),
                    "ref_text": analyte.get("ref_text"),
                })
        elif document.get("doc_type") == "doctor_report":
            reports.append({
                "id": filename,
                "visit_date": visit_date,
                "diagnosis": document.get("diagnosis"),
                "doctor_name": document.get("doctor_name"),
                "department": document.get("department"),
                "medications": [{"raw": item} for item in document.get("medications") or []],
                "recommendations": document.get("recommendations") or [],
            })
    return labs, reports, missing_dates


def _hash_package(package: dict) -> str:
    payload = {key: value for key, value in package.items() if key != "sha256"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_patient_packages(
    records: Iterable[dict],
    *,
    garmin_patient_key: str | None = None,
    garmin: list[dict] | None = None,
    garmin_activities: list[dict] | None = None,
) -> dict[str, dict]:
    packages = {}
    for key, documents in group_documents(records).items():
        name, birth_date = key.split("|", 1)
        labs, reports, missing_dates = _records_for_package(documents)
        garmin_attached = key == garmin_patient_key
        base = build_fact_package(
            labs=labs,
            reports=reports,
            health=garmin if garmin_attached and garmin else [],
            activities=garmin_activities if garmin_attached and garmin_activities else [],
            sources=[],
        )
        package = {
            "schema_version": base["schema_version"],
            "patient": {
                "patient_key": key,
                "name": name,
                "birth_date": None if birth_date == "unknown" else birth_date,
                "documents": [item["filename"] for item in documents],
                "garmin_attached": garmin_attached,
                "missing_dates": sorted(missing_dates),
            },
            "external": {"weather": {"available": False, "reason": "нет weather facts в e2e fixtures"}},
            "facts": base["facts"],
        }
        package["sha256"] = _hash_package(package)
        packages[key] = package
    return packages


def load_garmin(db_path: Path | str, user_id: int = 1) -> tuple[list[dict], list[dict]]:
    with sqlite3.connect(db_path) as conn:
        health = conn.execute(
            """
            SELECT metric || ':' || date(taken_at) AS id, provider, metric,
                   date(taken_at) AS date, ROUND(AVG(value_num), 4) AS average,
                   ROUND(MIN(value_num), 4) AS minimum, ROUND(MAX(value_num), 4) AS maximum,
                   COUNT(*) AS observations, unit, MAX(value_json) AS value_json
            FROM health_metrics WHERE user_id = ? AND value_num IS NOT NULL
            GROUP BY metric, date(taken_at), provider, unit ORDER BY metric, date(taken_at)
            """,
            (user_id,),
        ).fetchall()
        activities = conn.execute(
            """
            SELECT id, provider, external_id, activity_type, started_at,
                   duration_s, distance_m, calories, avg_hr, max_hr
            FROM health_activities WHERE user_id = ? ORDER BY started_at, id
            """,
            (user_id,),
        ).fetchall()
    return [dict(zip(("id", "provider", "metric", "date", "average", "minimum", "maximum", "observations", "unit", "value_json"), row)) for row in health], [dict(zip(("id", "provider", "external_id", "activity_type", "started_at", "duration_s", "distance_m", "calories", "avg_hr", "max_hr"), row)) for row in activities]


def load_expected_directory(directory: Path | str) -> list[dict]:
    root = Path(directory)
    records = []
    for path in sorted(root.glob("*.expected.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        item["filename"] = path.name.removesuffix(".expected.json")
        records.append(item)
    return records
