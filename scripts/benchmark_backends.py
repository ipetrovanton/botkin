"""Benchmark Ollama vs MLX vs vLLM on the same model + same documents.

Runs e2e extraction on tests/fixtures/documents for each backend,
collects: wall time, per-document time, accuracy, memory usage.
Outputs JSON + markdown table.

Usage:
    uv run scripts/benchmark_backends.py --backends ollama,mlx
    uv run scripts/benchmark_backends.py --backend mlx
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from botkin.llm.extract import run_analysis
from botkin.llm.client import get_backend


def run_benchmark(backend: str, docs_dir: Path, output: Path | None = None) -> dict:
    """Run e2e benchmark for a single backend."""
    os.environ["LLM_BACKEND"] = backend

    samples_dir = docs_dir / "samples"
    if not samples_dir.exists():
        print(f"[ERROR] samples dir not found: {samples_dir}")
        return {}

    # Find all documents
    docs = []
    for ext in ("*.pdf", "*.jpg", "*.jpeg", "*.png", "*.heic"):
        docs.extend(sorted(samples_dir.glob(ext)))

    if not docs:
        print(f"[ERROR] no documents found in {samples_dir}")
        return {}

    print(f"\n{'='*60}")
    print(f"Backend: {backend} | Documents: {len(docs)}")
    print(f"{'='*60}\n")

    results = {
        "backend": backend,
        "n_documents": len(docs),
        "per_doc": [],
        "total_wall_time": 0.0,
        "errors": [],
    }

    t_total = time.perf_counter()

    for i, doc_path in enumerate(docs, 1):
        print(f"  [{i}/{len(docs)}] {doc_path.name}...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            rows = run_analysis(doc_path)
            elapsed = time.perf_counter() - t0
            n_rows = len(rows)
            results["per_doc"].append({
                "doc": doc_path.name,
                "elapsed": round(elapsed, 2),
                "n_rows": n_rows,
            })
            print(f"{elapsed:.1f}s, {n_rows} rows")
        except Exception as e:
            elapsed = time.perf_counter() - t0
            results["errors"].append({
                "doc": doc_path.name,
                "error": str(e),
                "elapsed": round(elapsed, 2),
            })
            print(f"ERROR: {e}")

    results["total_wall_time"] = round(time.perf_counter() - t_total, 2)

    # Compute stats
    times = [d["elapsed"] for d in results["per_doc"]]
    if times:
        results["median_doc_time"] = round(statistics.median(times), 2)
        results["min_doc_time"] = round(min(times), 2)
        results["max_doc_time"] = round(max(times), 2)
        results["p95_doc_time"] = round(
            sorted(times)[int(len(times) * 0.95) - 1] if len(times) > 1 else times[0], 2
        )
        results["n_errors"] = len(results["errors"])
        results["n_success"] = len(results["per_doc"])

    print(f"\n  Total: {results['total_wall_time']}s")
    print(f"  Success: {results.get('n_success', 0)}/{len(docs)}")
    print(f"  Errors: {results.get('n_errors', 0)}")

    if output:
        output_path = output.parent / f"benchmark_{backend}.json"
        output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"  Saved: {output_path}")

    return results


def print_comparison_table(results: list[dict]) -> None:
    """Print markdown comparison table."""
    print(f"\n{'='*60}")
    print("## Benchmark Results\n")
    print("| Backend | Wall time | Median/doc | Min | Max | P95 | Success | Errors |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r['backend']} | {r.get('total_wall_time', 'N/A')}s | "
            f"{r.get('median_doc_time', 'N/A')}s | "
            f"{r.get('min_doc_time', 'N/A')}s | "
            f"{r.get('max_doc_time', 'N/A')}s | "
            f"{r.get('p95_doc_time', 'N/A')}s | "
            f"{r.get('n_success', 0)}/{r.get('n_documents', 0)} | "
            f"{r.get('n_errors', 0)} |"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark LLM backends")
    parser.add_argument(
        "--backends", default="ollama,mlx",
        help="Comma-separated list of backends (ollama, mlx, vllm)",
    )
    parser.add_argument(
        "--backend", default=None,
        help="Single backend to benchmark",
    )
    parser.add_argument(
        "--docs-dir", default="tests/fixtures/documents",
        help="Directory with test documents",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output directory for JSON results",
    )
    args = parser.parse_args()

    backends = args.backend.split(",") if args.backend else args.backends.split(",")
    docs_dir = Path(args.docs_dir)
    output_dir = Path(args.output) if args.output else None

    all_results = []
    for backend in backends:
        backend = backend.strip()
        result = run_benchmark(backend, docs_dir, output_dir)
        if result:
            all_results.append(result)

    if len(all_results) > 1:
        print_comparison_table(all_results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
