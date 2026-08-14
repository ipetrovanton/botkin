from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path("benchmarks/deep_model_benchmark_q8_full")
TOPIC_PATTERNS = (
    "мочев",
    "креатинин",
    "лимфоцит",
    "моноцит",
    "базофил",
    "нейтрофил",
    "garmin",
    "hrv",
    "сон",
    "стресс",
    "лекар",
    "грлс",
    "pubmed",
    "референс",
    "противореч",
)


def output_text(model: str, scenario: str, seed: int) -> str:
    safe = model.replace("/", "_").replace(":", "_")
    path = ROOT / safe / f"{scenario}_seed_{seed}.output.md"
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9]+", text.lower()))


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![a-zа-яё])\d+(?:[.,]\d+)?", text.lower()))


def _pairwise(values: list[str], key) -> float:
    if len(values) < 2:
        return 1.0
    scores = []
    for left in range(len(values)):
        for right in range(left + 1, len(values)):
            a, b = key(values[left]), key(values[right])
            scores.append(len(a & b) / len(a | b) if a | b else 1.0)
    return sum(scores) / len(scores)


def _quality_flags(text: str) -> dict[str, object]:
    lower = text.lower()
    refusal_markers = ("я не врач", "не могу помочь", "обратитесь к врачу", "не является диагнозом")
    headings = re.findall(r"^#{2,3}\s+.+$", text, flags=re.MULTILINE)
    return {
        "sections": len(headings),
        "russian": sum(0x400 <= ord(char) <= 0x4FF for char in text) > sum(char.isascii() and char.isalpha() for char in text),
        "refusal": any(marker in lower for marker in refusal_markers),
    }


def main() -> int:
    rows = json.loads((ROOT / "runs.json").read_text(encoding="utf-8"))
    print("MODEL | SCENARIO | SEED | WALL_S | OUT_TOK | OUT_CHARS | TOK/WALL | TOPICS | EVIDENCE | CPU_P95 | GPU_P95 | GPU_WH")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["model"]].append(row)
        telemetry = row.get("telemetry", {})
        metrics = telemetry.get("metrics", {})
        cpu_p95 = metrics.get("cpu_package_temp_c", {}).get("p95")
        gpu_p95 = metrics.get("gpu_temp_c", {}).get("p95")
        gpu_wh = telemetry.get("energy_wh", {}).get("gpu")
        wall = row.get("wall_s") or 0.0
        tps = row.get("output_tokens", 0) / wall if wall else 0.0
        text = output_text(row["model"], row["scenario"], row["seed"])
        topics = sum(bool(re.search(pattern, text.lower())) for pattern in TOPIC_PATTERNS) if row["scenario"] == "synthesis" else 0
        evidence = row.get("evidence", {})
        print(
            f"{row['model']} | {row['scenario']} | {row['seed']} | {wall:.1f} | "
            f"{row.get('output_tokens', 0)} | {row.get('output_chars', 0)} | {tps:.2f} | "
            f"{topics}/15 | {evidence.get('valid', 0)}/{evidence.get('cited', 0)} | "
            f"{cpu_p95} | {gpu_p95} | {gpu_wh}"
        )
    print("\nAGGREGATE")
    for model, model_rows in grouped.items():
        synthesis = [row for row in model_rows if row["scenario"] == "synthesis"]
        total_wall = sum(row.get("wall_s", 0.0) for row in synthesis)
        total_chars = sum(row.get("output_chars", 0) for row in synthesis)
        texts = [output_text(model, "synthesis", row["seed"]) for row in synthesis]
        hashes = [hashlib.sha256(text.encode()).hexdigest()[:12] for text in texts]
        flags = [_quality_flags(text) for text in texts]
        print(
            f"{model}: synthesis_wall={total_wall:.1f}s output_chars={total_chars} "
            f"token_jaccard={_pairwise(texts, _tokens):.3f} "
            f"number_jaccard={_pairwise(texts, _numbers):.3f} "
            f"exact_hashes={len(set(hashes))}/3 "
            f"sections={[flag['sections'] for flag in flags]} "
            f"russian={all(flag['russian'] for flag in flags)} "
            f"refusal={any(flag['refusal'] for flag in flags)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
