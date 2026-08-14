from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from deep_model_benchmark import FactAudit, validate_fact_audit


def _records_by_id(package: dict) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for domain in ("labs", "reports", "medications", "health", "activities", "sources"):
        for item in package["facts"].get(domain, []) or []:
            if item.get("id"):
                records[str(item["id"])] = item
    return records


def _lookup_date(records: dict[str, dict], evidence_id: str) -> str | None:
    record = records.get(evidence_id)
    if record is None:
        return None
    return record.get("taken_at") or record.get("visit_date") or record.get("started_at") or record.get("date")


def _lookup_report(record: dict | None) -> dict:
    if record is None:
        return {}
    return {
        "visit_date": record.get("visit_date"),
        "doctor_name": record.get("doctor_name"),
        "department": record.get("department"),
        "diagnosis": record.get("diagnosis"),
        "recommendations": record.get("recommendations") or [],
    }


def _lookup_medication(record: dict | None) -> dict:
    if record is None:
        return {}
    return {
        "raw": record.get("raw"),
        "canonical": record.get("canonical"),
        "report_id": record.get("report_id"),
    }


def _group_lab_assertions(audit: FactAudit, records: dict[str, dict]) -> list[dict]:
    grouped: dict[tuple[str, str | None], list[dict]] = defaultdict(list)
    for assertion in audit.lab_assertions:
        evidence_id = assertion.evidence_ids[0] if assertion.evidence_ids else None
        record = records.get(evidence_id) if evidence_id else None
        date = _lookup_date(records, evidence_id) if evidence_id else None
        grouped[(assertion.name, assertion.unit)].append({
            "evidence_id": evidence_id,
            "date": date,
            "value_num": assertion.value_num,
            "unit": assertion.unit,
            "status": assertion.status,
            "reference": record.get("reference") if record else None,
            "ref_low": record.get("ref_low") if record else None,
            "ref_high": record.get("ref_high") if record else None,
        })
    series = []
    for (name, unit), rows in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        ordered = sorted(rows, key=lambda row: (row["date"] or "", row["evidence_id"] or ""))
        values = [row["value_num"] for row in ordered if row["value_num"] is not None]
        series.append({
            "name": name,
            "unit": unit,
            "count": len(ordered),
            "date_from": ordered[0]["date"] if ordered else None,
            "date_to": ordered[-1]["date"] if ordered else None,
            "values": ordered,
            "average": round(sum(values) / len(values), 2) if values else None,
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        })
    return series


def _build_report_summary(audit: FactAudit, records: dict[str, dict]) -> list[dict]:
    summary = []
    for assertion in audit.date_assertions:
        evidence_id = assertion.evidence_ids[0] if assertion.evidence_ids else None
        record = records.get(evidence_id) if evidence_id else None
        summary.append({
            "evidence_id": evidence_id,
            "date": assertion.date,
            **_lookup_report(record),
        })
    return sorted(summary, key=lambda item: (item["date"] or "", item["evidence_id"] or ""))


def _build_medication_summary(audit: FactAudit, records: dict[str, dict]) -> list[dict]:
    summary = []
    for assertion in audit.medication_assertions:
        evidence_id = assertion.evidence_ids[0] if assertion.evidence_ids else None
        record = records.get(evidence_id) if evidence_id else None
        report_record = records.get(record.get("report_id")) if record and record.get("report_id") else None
        summary.append({
            "evidence_id": evidence_id,
            "raw": assertion.raw,
            "canonical": assertion.canonical,
            "schedule": assertion.schedule,
            "report_id": record.get("report_id") if record else None,
            "report_date": report_record.get("visit_date") if report_record else None,
        })
    return sorted(summary, key=lambda item: (item["report_date"] or "", item["evidence_id"] or ""))


def build_patient_summary(audit: FactAudit, package: dict, score: dict) -> dict[str, Any]:
    if not score.get("passed"):
        raise ValueError("нельзя строить patient summary из непроверенного audit")
    records = _records_by_id(package)
    return {
        "patient_key": package["patient"]["patient_key"],
        "birth_date": package["patient"].get("birth_date"),
        "garmin_attached": package["patient"].get("garmin_attached", False),
        "weather_available": package["external"].get("weather", {}).get("available", False),
        "source": "verified_patient_audit",
        "validation": {
            "passed": score["passed"],
            "invalid_ids": score["provenance"].get("invalid_ids", []),
            "total_ids": score["provenance"].get("total_ids", 0),
        },
        "labs": _group_lab_assertions(audit, records),
        "reports": _build_report_summary(audit, records),
        "medications": _build_medication_summary(audit, records),
        "contradictions": [
            {"description": item.description, "evidence_ids": item.evidence_ids}
            for item in audit.contradictions
        ],
        "findings": [
            {
                "type": item.type,
                "text": item.text,
                "confidence": item.confidence,
                "evidence_ids": item.evidence_ids,
            }
            for item in audit.findings
        ],
        "missing_data": list(audit.missing_data),
        "missing_dates": package["patient"].get("missing_dates", []),
    }


def render_markdown(summary: dict) -> str:
    lines = [
        "# Verified patient summary\n",
        f"Пациент: `{summary['patient_key']}`\n",
        f"Дата рождения: `{summary['birth_date'] or 'неизвестна'}`\n",
        f"Garmin: `{summary['garmin_attached']}`; погодные данные: `{'да' if summary['weather_available'] else 'нет'}`.\n",
        f"Источник: `{summary['source']}`; медицинская интерпретация не выполняется.\n",
        f"Validation: passed=`{summary['validation']['passed']}`, invalid IDs=`{summary['validation']['invalid_ids']}`, total IDs=`{summary['validation']['total_ids']}`.\n",
        "\n## Лабораторные показатели\n",
    ]
    for series in summary["labs"]:
        lines.append(f"\n### {series['name']} ({series['unit'] or 'без единицы'}) — {series['count']} измерений\n")
        lines.append("| Date | Value | Status | Ref low | Ref high | Evidence |\n|---|---:|---|---:|---:|---|\n")
        for row in series["values"]:
            ref_low = row.get("ref_low")
            ref_high = row.get("ref_high")
            ref_low_text = f"{ref_low}" if ref_low is not None else "—"
            ref_high_text = f"{ref_high}" if ref_high is not None else "—"
            lines.append(f"| {row['date'] or 'N/A'} | {row['value_num']} | {row['status']} | {ref_low_text} | {ref_high_text} | `{row['evidence_id']}` |\n")
        if series["count"] > 1:
            lines.append(f"_Min={series['minimum']}, Max={series['maximum']}, Avg={series['average']}_\n")

    lines.extend(["\n## Заключения врачей\n", "| Date | Doctor | Department | Diagnosis | Recommendations | Evidence |\n|---|---|---|---|---|---|\n"])
    for item in summary["reports"]:
        recommendations = "; ".join(item.get("recommendations") or []) or "—"
        lines.append(
            f"| {item['date'] or 'N/A'} | {item.get('doctor_name') or '—'} | {item.get('department') or '—'} | "
            f"{item.get('diagnosis') or '—'} | {recommendations} | `{item['evidence_id']}` |\n"
        )

    lines.extend(["\n## Назначенные препараты\n", "| Raw | Canonical | Schedule | Report date | Evidence |\n|---|---|---|---|---|\n"])
    for item in summary["medications"]:
        lines.append(
            f"| {item['raw'] or '—'} | {item['canonical'] or '—'} | {item['schedule'] or '—'} | "
            f"{item['report_date'] or 'N/A'} | `{item['evidence_id']}` |\n"
        )

    if summary["contradictions"]:
        lines.extend(["\n## Выявленные противоречия\n"])
        for item in summary["contradictions"]:
            lines.append(f"- {item['description']} — evidence: {', '.join(f'`{e}`' for e in item['evidence_ids'])}\n")

    if summary["findings"]:
        lines.extend(["\n## Ключевые выводы (из audit)\n", "| Type | Confidence | Text | Evidence |\n|---|---|---|---|\n"])
        for item in summary["findings"]:
            evidence = ", ".join(f"`{e}`" for e in item["evidence_ids"])
            lines.append(f"| {item['type']} | {item['confidence']} | {item['text']} | {evidence} |\n")

    if summary["missing_data"]:
        lines.extend(["\n## Недостающие данные (из audit)\n"])
        for item in summary["missing_data"]:
            lines.append(f"- {item}\n")

    if summary["missing_dates"]:
        lines.extend(["\n## Документы с нераспознанной датой\n"])
        for item in summary["missing_dates"]:
            lines.append(f"- `{item}`\n")

    return "".join(lines)


def _format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "N/A"
    unit_text = f" {unit}" if unit else ""
    return f"{value}{unit_text}"


def render_report(summary: dict) -> str:
    lines = [
        "# Verified patient report\n",
        f"Пациент: `{summary['patient_key']}`\n",
        f"Дата рождения: `{summary['birth_date'] or 'неизвестна'}`\n",
        f"Garmin: `{summary['garmin_attached']}`; погодные данные: `{'да' if summary['weather_available'] else 'нет'}`.\n",
        "Источник: `verified_patient_audit` + `verified_garmin_summary` при наличии.\n",
        f"Validation: passed=`{summary['validation']['passed']}`, invalid IDs=`{summary['validation']['invalid_ids']}`, total IDs=`{summary['validation']['total_ids']}`.\n",
        "\n## Лабораторные отклонения\n",
    ]
    abnormal = [series for series in summary["labs"] if any(row["status"] in ("low", "high") for row in series["values"])]
    if abnormal:
        for series in sorted(abnormal, key=lambda item: item["name"]):
            lines.append(f"\n### {series['name']} ({series['unit'] or 'без единицы'})\n")
            for row in series["values"]:
                if row["status"] not in ("low", "high"):
                    continue
                ref_low = row.get("ref_low")
                ref_high = row.get("ref_high")
                ref = ""
                if ref_low is not None and ref_high is not None:
                    ref = f" (реф. {ref_low}–{ref_high})"
                elif ref_low is not None:
                    ref = f" (реф. ≥ {ref_low})"
                elif ref_high is not None:
                    ref = f" (реф. ≤ {ref_high})"
                lines.append(
                    f"- **{row['status'].upper()}**: {row['value_num']}{' ' + series['unit'] if series['unit'] else ''}{ref} "
                    f"[{row['evidence_id']}]\n"
                )
    else:
        lines.append("- Лабораторных отклонений, отмеченных в аудите, не выявлено.\n")

    lines.extend(["\n## Заключения врачей\n"])
    if summary["reports"]:
        lines.append("| Date | Doctor | Department | Diagnosis | Recommendations | Evidence |\n|---|---|---|---|---|---|\n")
        for item in summary["reports"]:
            recommendations = "; ".join(item.get("recommendations") or []) or "—"
            lines.append(
                f"| {item['date'] or 'N/A'} | {item.get('doctor_name') or '—'} | {item.get('department') or '—'} | "
                f"{item.get('diagnosis') or '—'} | {recommendations} | `{item['evidence_id']}` |\n"
            )
    else:
        lines.append("- Заключений врачей в аудите нет.\n")

    lines.extend(["\n## Назначенные препараты\n"])
    if summary["medications"]:
        lines.append("| Препарат | Schedule | Date | Evidence |\n|---|---|---|---|\n")
        for item in summary["medications"]:
            name = item['canonical'] or item['raw'] or '—'
            lines.append(
                f"| {name} | {item['schedule'] or '—'} | {item['report_date'] or 'N/A'} | "
                f"`{item['evidence_id']}` |\n"
            )
    else:
        lines.append("- Препаратов в аудите нет.\n")

    if summary.get("contradictions"):
        lines.extend(["\n## Выявленные противоречия\n"])
        for item in summary["contradictions"]:
            evidence = ", ".join(f"`{e}`" for e in item["evidence_ids"])
            lines.append(f"- {item['description']} — {evidence}\n")

    if summary["findings"]:
        lines.extend(["\n## Ключевые выводы аудита\n"])
        for item in summary["findings"]:
            evidence = ", ".join(f"`{e}`" for e in item["evidence_ids"])
            lines.append(f"- **{item['type']}** ({item['confidence']}): {item['text']} — {evidence}\n")

    lines.extend(["\n## Что обсудить с врачом\n"])
    if abnormal or summary["findings"]:
        for series in sorted(abnormal, key=lambda item: item["name"]):
            for row in series["values"]:
                if row["status"] in ("low", "high"):
                    lines.append(
                        f"- Лабораторный показатель `{series['name']}` {row['status'].upper()} "
                        f"{_format_value(row['value_num'], series['unit'])} — обсудить динамику и причины [{row['evidence_id']}]\n"
                    )
        for item in summary["findings"]:
            if item["type"] in ("ИНТЕРПРЕТАЦИЯ", "ГИПОТЕЗА"):
                evidence = ", ".join(f"`{e}`" for e in item["evidence_ids"])
                lines.append(
                    f"- {item['type']} (confidence={item['confidence']}): {item['text']} — обсудить проверку — {evidence}\n"
                )
    else:
        lines.append("- На основе аудита нет отклонений или гипотез для обсуждения.\n")

    lines.extend(["\n## Недостающие данные\n"])
    if summary["missing_data"]:
        for item in summary["missing_data"]:
            lines.append(f"- {item}\n")
    else:
        lines.append("- Нет.\n")
    if summary["missing_dates"]:
        lines.append("\nДокументы с нераспознанной датой:\n")
        for item in summary["missing_dates"]:
            lines.append(f"- `{item}`\n")

    lines.append("\n---\n")
    lines.append("Этот отчёт построен детерминированно из verified_patient_audit. Медицинская интерпретация и окончательные решения остаются за врачом.\n")
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = json.loads(args.package.read_text(encoding="utf-8"))
    audit = validate_fact_audit(json.loads(args.audit.read_text(encoding="utf-8")))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    summary = build_patient_summary(audit, package, result["score"])
    args.output.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(summary), encoding="utf-8")
    args.output.with_suffix(".report.md").write_text(render_report(summary), encoding="utf-8")
    print(f"created={args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
