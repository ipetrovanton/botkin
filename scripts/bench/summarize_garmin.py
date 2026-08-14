from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from garmin_audit import GarminAudit, validate_garmin_audit


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _mean(values: list[float]) -> float | None:
    return _round(sum(values) / len(values)) if values else None


def build_garmin_summary(audit: GarminAudit, package: dict, score: dict) -> dict:
    if not score.get("passed"):
        raise ValueError("нельзя строить summary из непроверенного Garmin audit")
    metrics_by_name: dict[str, list] = defaultdict(list)
    for item in audit.metrics:
        metrics_by_name[item.metric].append(item)
    metric_summary = []
    for metric, items in sorted(metrics_by_name.items()):
        values = [item.value_num for item in items]
        ordered = sorted(items, key=lambda item: item.date)
        metric_summary.append({
            "metric": metric,
            "unit": items[0].unit,
            "count": len(items),
            "date_from": ordered[0].date,
            "date_to": ordered[-1].date,
            "average": _mean(values),
            "minimum": _round(min(values)),
            "maximum": _round(max(values)),
            "evidence_ids": [item.evidence_ids[0] for item in ordered],
        })
    sleep_days = []
    for item in sorted(audit.metrics, key=lambda item: item.date):
        if item.metric != "sleep_seconds":
            continue
        sleep_days.append({
            "date": item.date,
            "total_seconds": item.value_num,
            "total_hours": _round(item.value_num / 3600),
            "phases_seconds": {key: _round(value) for key, value in sorted(item.sleep_phases.items())},
            "evidence_id": item.evidence_ids[0],
        })
    activities = []
    for item in sorted(audit.activities, key=lambda item: (item.date, item.evidence_ids[0])):
        activities.append({
            "date": item.date,
            "activity_type": item.activity_type,
            "duration_s": _round(item.duration_s),
            "distance_m": _round(item.distance_m),
            "avg_hr": _round(item.avg_hr),
            "max_hr": _round(item.max_hr),
            "calories": _round(item.calories),
            "evidence_id": item.evidence_ids[0],
        })
    return {
        "patient_key": package["patient"]["patient_key"],
        "source": "verified_garmin_audit",
        "coverage": {
            "evidence_ids": score["provenance"]["cited"],
            "metrics": score["metrics"]["matched"],
            "sleep_days": score["sleep"]["with_phases"],
            "activities": score["activities"]["matched"],
        },
        "metrics": metric_summary,
        "sleep": {
            "days": sleep_days,
            "average_hours": _mean([item["total_hours"] for item in sleep_days]),
        },
        "activities": {
            "items": activities,
            "total_duration_s": _round(sum(item["duration_s"] or 0 for item in activities)),
            "total_distance_m": _round(sum(item["distance_m"] or 0 for item in activities)),
            "total_calories": _round(sum(item["calories"] or 0 for item in activities)),
        },
        "interpretation": "none; this is a deterministic factual summary",
    }


def render_markdown(summary: dict) -> str:
    coverage = summary["coverage"]
    lines = [
        "# Verified Garmin summary\n",
        f"Пациент: `{summary['patient_key']}`\n",
        "Источник: `verified_garmin_audit`; медицинская интерпретация не выполняется.\n",
        f"Coverage: `{coverage['metrics']} metrics`, `{coverage['sleep_days']} sleep days`, "
        f"`{coverage['activities']} activities`, `{coverage['evidence_ids']} evidence IDs`.\n",
        "\n## Сводка метрик\n",
        "| Метрика | Период | N | Среднее | Min | Max | Unit |\n|---|---|---:|---:|---:|---:|---|\n",
    ]
    for item in summary["metrics"]:
        lines.append(
            f"| `{item['metric']}` | {item['date_from']} — {item['date_to']} | {item['count']} | "
            f"{item['average']} | {item['minimum']} | {item['maximum']} | `{item['unit']}` |\n"
        )
    lines.extend(["\n## Сон\n", f"Средняя длительность: **{summary['sleep']['average_hours']} ч**.\n\n", "| Date | Total h | Deep s | Light s | REM s | Awake s | Evidence |\n|---|---:|---:|---:|---:|---:|---|\n"])
    for item in summary["sleep"]["days"]:
        phases = item["phases_seconds"]
        lines.append(
            f"| {item['date']} | {item['total_hours']} | {phases.get('deepSleepSeconds')} | "
            f"{phases.get('lightSleepSeconds')} | {phases.get('remSleepSeconds')} | "
            f"{phases.get('awakeSleepSeconds')} | `{item['evidence_id']}` |\n"
        )
    lines.extend(["\n## Активности\n", "| Date | Type | Duration s | Distance m | Avg HR | Max HR | Calories | Evidence |\n|---|---|---:|---:|---:|---:|---:|---|\n"])
    for item in summary["activities"]["items"]:
        lines.append(
            f"| {item['date']} | `{item['activity_type']}` | {item['duration_s']} | {item['distance_m']} | "
            f"{item['avg_hr']} | {item['max_hr']} | {item['calories']} | `{item['evidence_id']}` |\n"
        )
    totals = summary["activities"]
    lines.append(
        f"\nTotals: duration `{totals['total_duration_s']} s`, distance `{totals['total_distance_m']} m`, "
        f"calories `{totals['total_calories']}`.\n"
    )
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = json.loads(args.package.read_text(encoding="utf-8"))
    audit = validate_garmin_audit(json.loads(args.audit.read_text(encoding="utf-8")))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    summary = build_garmin_summary(audit, package, result["score"])
    args.output.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(summary), encoding="utf-8")
    print(f"created={args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
