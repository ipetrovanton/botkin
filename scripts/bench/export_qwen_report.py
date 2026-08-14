from pathlib import Path


SOURCE_DIR = Path("benchmarks/deep_model_benchmark_q8_full") / "huihui_ai_Qwen3.6-abliterated_35b-a3b"
TARGET = SOURCE_DIR / "qwen_full_synthesis_report.md"


def main() -> int:
    sections = [
        "# Qwen3.6-35B-A3B — полный synthesis-отчёт\n\n"
        "Модель: `huihui_ai/Qwen3.6-abliterated:35b-a3b`\n"
        "Режим: q8 KV-cache, Flash Attention, `num_ctx=32768`, `think=high`\n"
        "Fact package SHA-256: `c04efae696a780716ba8500fd697fe285974e6fae42fc7ae0655ea271f3081bf`\n\n"
        "Ниже приведены полные ответы модели для трёх seed. Это локальный private artifact;"
        " не переносить в публичную фактуру без обезличивания.\n"
    ]
    for seed in (42, 43, 44):
        path = SOURCE_DIR / f"synthesis_seed_{seed}.output.md"
        sections.append(f"\n\n---\n\n## Synthesis seed {seed}\n\n")
        sections.append(path.read_text(encoding="utf-8"))
    TARGET.write_text("".join(sections), encoding="utf-8")
    print(f"created={TARGET} bytes={TARGET.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
