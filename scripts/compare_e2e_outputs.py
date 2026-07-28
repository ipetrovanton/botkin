"""Compare e2e extraction output against expected JSON fixtures.

Usage:
    uv run scripts/compare_e2e_outputs.py [--docs-dir tests/fixtures/documents]

Exit code 0 if all match, 1 if any mismatch.
"""

import argparse
import json
import sys
from pathlib import Path

from botkin.llm.extract import run_analysis


def load_expected(fixture_dir: Path) -> dict[str, list[dict]]:
    """Load all *.expected.json files from fixture_dir/samples/."""
    samples_dir = fixture_dir / "samples"
    expected: dict[str, list[dict]] = {}
    for f in sorted(samples_dir.glob("*.expected.json")):
        expected[f.stem.replace(".expected", "")] = json.loads(f.read_text())
    return expected


def compare_row(actual: dict, expected: dict) -> list[str]:
    """Compare a single LabResult dict against expected. Returns list of diffs."""
    diffs = []
    for key in ("analyte_name", "value_num", "value_text", "value_raw", "unit",
                "ref_low", "ref_high", "ref_operator", "ref_text"):
        a = actual.get(key)
        e = expected.get(key)
        if a != e:
            diffs.append(f"  {key}: expected={e!r}, got={a!r}")
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-dir", default="tests/fixtures/documents")
    args = parser.parse_args()

    fixture_dir = Path(args.docs_dir)
    expected = load_expected(fixture_dir)

    if not expected:
        print("No expected fixtures found")
        return 1

    total = 0
    matched = 0
    failed = 0

    for sample_name, expected_rows in sorted(expected.items()):
        total += 1
        sample_path = fixture_dir / "samples" / f"{sample_name}.pdf"
        if not sample_path.exists():
            for ext in (".jpg", ".jpeg", ".png", ".heic"):
                p = fixture_dir / "samples" / f"{sample_name}{ext}"
                if p.exists():
                    sample_path = p
                    break

        if not sample_path.exists():
            print(f"[SKIP] {sample_name}: source file not found")
            continue

        try:
            actual_rows = run_analysis(sample_path)
        except Exception as e:
            print(f"[ERROR] {sample_name}: {e}")
            failed += 1
            continue

        actual_dicts = [r.model_dump() for r in actual_rows]

        if len(actual_dicts) != len(expected_rows):
            print(f"[MISMATCH] {sample_name}: row count {len(actual_dicts)} vs {len(expected_rows)}")
            failed += 1
            continue

        all_match = True
        for i, (a, e) in enumerate(zip(actual_dicts, expected_rows)):
            diffs = compare_row(a, e)
            if diffs:
                all_match = False
                print(f"[DIFF] {sample_name} row {i}:")
                for d in diffs:
                    print(d)

        if all_match:
            matched += 1
            print(f"[OK] {sample_name}: {len(actual_dicts)} rows match")
        else:
            failed += 1

    print(f"\nSummary: {matched}/{total} matched, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
