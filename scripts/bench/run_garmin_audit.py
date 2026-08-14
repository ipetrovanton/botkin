from __future__ import annotations

import argparse
import json
from pathlib import Path

from deep_model_benchmark import _chat, _stop_all_models, atomic_write_json
from garmin_audit import (
    build_batches,
    build_prompt,
    garmin_json_schema,
    merge_audits,
    score_garmin_audit,
    validate_garmin_audit,
)
from hardware_telemetry import TelemetrySession
from summarize_garmin import build_garmin_summary, render_markdown

MODELS = ("huihui_ai/Qwen3.6-abliterated:35b-a3b", "gemma4:26b-a4b-it-qat")


def write_report(path: Path, package: dict, result: dict, merged: dict) -> None:
    score = result["score"]
    lines = [
        "# Garmin structured audit\n",
        f"Пациент: `{package['patient']['patient_key']}`\n",
        f"Модель: `{result['model']}`\n",
        f"Fact package SHA-256: `{package['sha256']}`\n",
        "Режим: `temperature=0`, `think=false`, JSON Schema, batches по 40 метрик.\n",
        "\n## Score\n",
        f"- passed: `{score['passed']}`\n",
        f"- cited evidence IDs: `{score['provenance']['cited']}`\n",
        f"- invalid IDs: `{score['provenance']['invalid_ids']}`\n",
        f"- metrics matched: `{score['metrics']['matched']}` / `{len(package['facts']['health'])}`\n",
        f"- sleep phases: `{score['sleep']['with_phases']}` / `{score['sleep']['expected']}`\n",
        f"- activities matched: `{score['activities']['matched']}` / `{len(package['facts']['activities'])}`\n",
        "\n## Метрики Garmin\n",
        "| Evidence ID | Metric | Date | Value | Unit | Sleep phases |\n|---|---|---|---:|---|---|\n",
    ]
    for item in merged["metrics"]:
        phases = json.dumps(item.get("sleep_phases"), ensure_ascii=False) if item.get("sleep_phases") else ""
        lines.append(f"| `{item['evidence_ids'][0]}` | `{item['metric']}` | `{item['date']}` | {item['value_num']} | `{item['unit']}` | `{phases}` |\n")
    lines.extend(["\n## Активности Garmin\n", "| Evidence ID | Type | Date | Duration s | Distance m | Avg HR | Max HR | Calories |\n|---|---|---|---:|---:|---:|---:|---:|\n"])
    for item in merged["activities"]:
        lines.append(f"| `{item['evidence_ids'][0]}` | `{item.get('activity_type')}` | `{item['date']}` | {item.get('duration_s')} | {item.get('distance_m')} | {item.get('avg_hr')} | {item.get('max_hr')} | {item.get('calories')} |\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=Path("benchmarks/e2e_patient_packages/Петров Антон Игоревич__24.02.1993.json"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/garmin_audit"))
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--health-batch-size", type=int, default=20)
    args = parser.parse_args()
    package = json.loads(args.package.read_text(encoding="utf-8"))
    batches = build_batches(package, health_batch_size=args.health_batch_size)
    all_results = []
    for model in MODELS:
        _stop_all_models()
        model_dir = args.output / model.replace("/", "_").replace(":", "_")
        model_dir.mkdir(parents=True, exist_ok=True)
        audits = []
        batch_results = []
        for index, batch in enumerate(batches):
            system, user = build_prompt(package, batch)
            prefix = model_dir / f"batch_{index:03d}_{batch['domain']}"
            record = {"domain": batch["domain"], "expected_ids": batch["expected_ids"], "error": None}
            try:
                with TelemetrySession(prefix, interval_s=1.0) as session:
                    session.wait_ready()
                    response, elapsed = _chat(
                        model,
                        system,
                        user,
                        {"think": False, "temperature": 0.0, "num_ctx": 8192, "num_predict": 8192, "format_schema": garmin_json_schema()},
                        seed=42,
                        timeout_s=args.timeout,
                    )
                content = (response.get("message") or {}).get("content") or ""
                prefix.with_suffix(".output.json").write_text(content, encoding="utf-8")
                parsed = validate_garmin_audit(json.loads(content))
                audits.append(parsed.model_dump())
                record.update({"wall_s": elapsed, "output_chars": len(content), "telemetry": session.summary()})
            except Exception as exc:
                record["error"] = str(exc)
            batch_results.append(record)
            atomic_write_json(model_dir / "batches.json", batch_results)
        merged = merge_audits(audits)
        merged_model = validate_garmin_audit(merged)
        score = score_garmin_audit(merged_model, package)
        result = {"model": model, "score": score, "batches": batch_results}
        atomic_write_json(model_dir / "merged.audit.json", merged)
        atomic_write_json(model_dir / "result.json", result)
        write_report(model_dir / "garmin_audit.md", package, result, merged)
        if score["passed"]:
            summary = build_garmin_summary(merged_model, package, score)
            (model_dir / "verified_garmin_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            (model_dir / "verified_garmin_summary.md").write_text(render_markdown(summary), encoding="utf-8")
        all_results.append(result)
        print(json.dumps({"model": model, "score": score}, ensure_ascii=False), flush=True)
        _stop_all_models()
    atomic_write_json(args.output / "results.json", all_results)
    return 0 if all(not any(batch.get("error") for batch in item["batches"]) for item in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
