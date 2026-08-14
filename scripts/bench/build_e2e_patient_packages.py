from __future__ import annotations

import argparse
import json
from pathlib import Path

from e2e_patient_facts import build_patient_packages, load_expected_directory, load_garmin


GARMIN_PATIENT_KEY = "Петров Антон Игоревич|24.02.1993"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=Path("tests/fixtures/documents/samples"))
    parser.add_argument("--db", type=Path, default=Path("data/botkin.db"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/e2e_patient_packages"))
    args = parser.parse_args()
    records = load_expected_directory(args.fixtures)
    garmin, activities = load_garmin(args.db)
    packages = build_patient_packages(
        records,
        garmin_patient_key=GARMIN_PATIENT_KEY,
        garmin=garmin,
        garmin_activities=activities,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    summary = []
    for key, package in packages.items():
        safe = key.replace("|", "__").replace("/", "_")
        path = args.output / f"{safe}.json"
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append({
            "patient_key": key,
            "path": str(path),
            "documents": len(package["patient"]["documents"]),
            "labs": len(package["facts"]["labs"]),
            "reports": len(package["facts"]["reports"]),
            "medications": len(package["facts"]["medications"]),
            "health": len(package["facts"]["health"]),
            "activities": len(package["facts"]["activities"]),
            "garmin_attached": package["patient"]["garmin_attached"],
            "missing_dates": package["patient"]["missing_dates"],
            "sha256": package["sha256"],
        })
    (args.output / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
