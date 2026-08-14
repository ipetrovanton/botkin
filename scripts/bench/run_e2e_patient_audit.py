from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from deep_model_benchmark import _stop_all_models, atomic_write_json, validate_fact_audit
from structured_audit import run_one
from summarize_patient import build_patient_summary, render_markdown, render_report

MODELS = (
    "huihui_ai/Qwen3.6-abliterated:35b-a3b",
    "gemma4:26b-a4b-it-qat",
)


def _safe_key(key: str) -> str:
    return re.sub(r"[^\w.-]+", "_", key)


def load_patient_packages(packages_dir: Path, patient_key: str | None = None) -> list[dict]:
    packages = []
    for path in sorted(packages_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        package = json.loads(path.read_text(encoding="utf-8"))
        if patient_key is not None and package["patient"]["patient_key"] != patient_key:
            continue
        packages.append(package)
    return packages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", type=Path, default=Path("benchmarks/e2e_patient_packages"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/e2e_patient_audit"))
    parser.add_argument("--patient-key", type=str, default=None)
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--lab-batch-size", type=int, default=32)
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    packages = load_patient_packages(args.packages, patient_key=args.patient_key)
    all_results = []
    for package in packages:
        patient_key = package["patient"]["patient_key"]
        patient_dir = args.output / _safe_key(patient_key)
        patient_dir.mkdir(parents=True, exist_ok=True)
        for model in args.models:
            result = run_one(
                model,
                package,
                patient_dir,
                args.timeout,
                include_findings=True,
                lab_batch_size=args.lab_batch_size,
            )
            all_results.append({
                "model": model,
                "patient_key": patient_key,
                "score": result.get("score"),
                "error": result.get("error"),
            })
            if not result.get("error") and result.get("score", {}).get("passed"):
                model_safe = model.replace("/", "_").replace(":", "_")
                merged_path = patient_dir / model_safe / "merged.audit.json"
                merged = json.loads(merged_path.read_text(encoding="utf-8"))
                audit = validate_fact_audit(merged)
                summary = build_patient_summary(audit, package, result["score"])
                (patient_dir / model_safe / "verified_patient_summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (patient_dir / model_safe / "verified_patient_summary.md").write_text(
                    render_markdown(summary), encoding="utf-8"
                )
                (patient_dir / model_safe / "verified_patient_report.md").write_text(
                    render_report(summary), encoding="utf-8"
                )
            passed = bool(result.get("score", {}).get("passed") and not result.get("error"))
            print(
                json.dumps(
                    {"model": model, "patient_key": patient_key, "passed": passed, "error": result.get("error")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            _stop_all_models()
        atomic_write_json(args.output / "results.json", all_results)
    return 0 if all(not item.get("error") for item in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
