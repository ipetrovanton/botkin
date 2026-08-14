from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping

from botkin.clinical.facts import classify_value, parse_reference_range


SCHEMA_VERSION = 1


def _row(row: Mapping[str, object]) -> dict[str, object]:
    return dict(row)


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lab_fact(row: Mapping[str, object]) -> dict[str, object]:
    item = _row(row)
    document_id = item.get("document_id")
    result_id = item.get("id")
    if document_id is None or result_id is None:
        raise ValueError("каждый lab result должен содержать document_id и id")
    raw_low = _number(item.get("ref_low"))
    raw_high = _number(item.get("ref_high"))
    ref_text = _text(item.get("ref_text"))
    ref_low, ref_high = raw_low, raw_high
    if ref_low is None and ref_high is None:
        ref_low, ref_high = parse_reference_range(ref_text)
    value_num = _number(item.get("value_num"))
    return {
        "id": f"LAB:{document_id}:{result_id}",
        "document_id": document_id,
        "result_id": result_id,
        "taken_at": _text(item.get("taken_at")),
        "name": _text(item.get("name")) or _text(item.get("raw_name")) or "",
        "raw_name": _text(item.get("raw_name")),
        "value_num": value_num,
        "value_text": _text(item.get("value_text")),
        "unit": _text(item.get("unit")),
        "unit_raw": _text(item.get("unit_raw")),
        "reference": ref_text,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "status": classify_value(value_num, ref_low, ref_high),
    }


def _report_fact(row: Mapping[str, object]) -> tuple[dict[str, object], list[dict[str, object]]]:
    item = _row(row)
    report_id = item.get("id")
    if report_id is None:
        raise ValueError("каждое заключение должно содержать id")
    report_key = f"REP:{report_id}"
    medications = []
    for index, medication in enumerate(item.get("medications") or []):
        med = _row(medication) if isinstance(medication, Mapping) else {"raw": medication}
        medications.append(
            {
                "id": f"MED:{report_id}:{index}",
                "report_id": report_key,
                "raw": _text(med.get("raw")) or _text(med.get("name")),
                "canonical": _text(med.get("canonical")),
                "schedule": _text(med.get("schedule")),
            }
        )
    return (
        {
            "id": report_key,
            "report_id": report_id,
            "visit_date": _text(item.get("visit_date")),
            "diagnosis": _text(item.get("diagnosis")),
            "doctor_name": _text(item.get("doctor_name")),
            "department": _text(item.get("department")),
            "recommendations": [_text(value) for value in item.get("recommendations") or [] if _text(value)],
        },
        medications,
    )


def _stable_records(records: Iterable[Mapping[str, object]], prefix: str) -> list[dict[str, object]]:
    normalized = []
    for row in records:
        item = _row(row)
        record_id = item.get("id")
        if record_id is None:
            raise ValueError(f"каждая запись {prefix} должна содержать id")
        normalized.append({**item, "id": f"{prefix}:{record_id}"})
    return sorted(normalized, key=lambda item: str(item["id"]))


def _lab_series(labs: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str | None], list[dict[str, object]]] = defaultdict(list)
    for lab in labs:
        groups[(str(lab["name"]), lab["unit"] if isinstance(lab["unit"], str) else None)].append(lab)
    series = []
    for (name, unit), rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        ordered = sorted(rows, key=lambda item: (item["taken_at"] or "", str(item["id"])))
        series.append(
            {
                "name": name,
                "unit": unit,
                "fact_ids": [str(item["id"]) for item in ordered],
                "observations": len(ordered),
            }
        )
    return series


def _canonical_json(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_fact_package(
    *,
    labs: Iterable[Mapping[str, object]],
    reports: Iterable[Mapping[str, object]],
    health: Iterable[Mapping[str, object]],
    activities: Iterable[Mapping[str, object]],
    sources: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    lab_facts = sorted(
        (_lab_fact(row) for row in labs),
        key=lambda item: (item["taken_at"] or "", str(item["id"])),
    )
    report_pairs = [_report_fact(row) for row in reports]
    report_facts = sorted((report for report, _ in report_pairs), key=lambda item: (item["visit_date"] or "", str(item["id"])))
    medications = sorted((med for _, meds in report_pairs for med in meds), key=lambda item: str(item["id"]))
    facts = {
        "labs": lab_facts,
        "lab_series": _lab_series(lab_facts),
        "reports": report_facts,
        "medications": medications,
        "health": _stable_records(health, "HLT"),
        "activities": _stable_records(activities, "ACT"),
        "sources": _stable_records(sources, "SRC"),
    }
    payload = {"schema_version": SCHEMA_VERSION, "facts": facts}
    return {**payload, "sha256": hashlib.sha256(_canonical_json(payload)).hexdigest()}
