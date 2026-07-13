"""Быстрая проверка прогресса e2e-теста."""
from pathlib import Path
log_path = Path(__file__).resolve().parent.parent.parent / "bench_qwen3-vl_8b-instruct.log"
data = log_path.read_bytes()
text = data.decode("utf-8", "replace")
lines = text.splitlines()
count = text.count("[E2E]")
with open("bench_progress.txt", "w", encoding="utf-8") as f:
    f.write(f"E2E блоков: {count}\n\n")
    for line in lines[-10:]:
        f.write(line + "\n")
print(f"E2E блоков: {count}")
