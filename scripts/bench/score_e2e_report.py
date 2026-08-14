from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from deep_model_benchmark import all_fact_ids


_EVIDENCE_ID_RE = re.compile(r"(?:\[|`)([A-Z]+:[^`\]\s]+)(?:\]|`)")


def extract_evidence_ids(text: str) -> set[str]:
    return set(_EVIDENCE_ID_RE.findall(text))


def score_e2e_report(text: str, package: dict, summary: dict | None = None) -> dict:
    valid_ids = all_fact_ids(package)
    cited = extract_evidence_ids(text)
    invalid_ids = sorted(cited - valid_ids)
    src_only = sorted({evidence_id for evidence_id in cited if evidence_id.startswith("SRC:")})
    lower = text.lower()

    garmin_mentioned = any(word in lower for word in ("garmin", "hrv", "body battery"))
    garmin_absent = bool(re.search(
        r"(нет|отсутств|не предостав|не подключ).{0,80}(garmin|hrv|body battery)|"
        r"(garmin|hrv|body battery).{0,80}(нет|отсутств|не предостав)",
        lower,
    ))
    garmin_leak = not package["patient"]["garmin_attached"] and garmin_mentioned and not garmin_absent

    weather_mentioned = any(word in lower for word in ("погода", "температура воздуха", "осадки", "давление атмосфер"))
    weather_absent = bool(re.search(
        r"(нет|отсутств|не предостав).{0,80}(погод|осадк|атмосфер)|"
        r"(погод|осадк|атмосфер).{0,80}(нет|отсутств|не предостав)",
        lower,
    ))
    weather_leak = not package["external"]["weather"]["available"] and weather_mentioned and not weather_absent

    claim_lines = 0
    cited_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|---") or stripped.startswith("-"):
            continue
        claim_lines += 1
        if _EVIDENCE_ID_RE.search(stripped):
            cited_lines += 1

    return {
        "citation_count": len(cited),
        "invalid_ids": invalid_ids,
        "src_citations": src_only,
        "garmin_leak": garmin_leak,
        "weather_leak": weather_leak,
        "passed_guards": not (invalid_ids or src_only or garmin_leak or weather_leak),
        "claim_lines": claim_lines,
        "cited_lines": cited_lines,
        "citation_ratio": round(cited_lines / claim_lines, 3) if claim_lines else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = args.report.read_text(encoding="utf-8")
    package = json.loads(args.package.read_text(encoding="utf-8"))
    score = score_e2e_report(text, package)
    if args.output:
        args.output.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(score, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
