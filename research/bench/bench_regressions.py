"""Извлекает детали проблемных документов из лога."""
data = open("bench_qwen3-vl_8b-instruct.log", "rb").read()
text = data.decode("utf-8", "replace")
lines = text.splitlines()

targets = ["sample_001.pdf", "sample_006.pdf", "sample_011.pdf"]
out = []
for target in targets:
    out.append(f"\n{'='*70}")
    in_block = False
    for line in lines:
        if f"[E2E] {target}" in line:
            in_block = True
        elif in_block and line.startswith("=" * 20):
            break
        if in_block:
            out.append(line)

with open("bench_regressions.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"Записано {len(out)} строк")
