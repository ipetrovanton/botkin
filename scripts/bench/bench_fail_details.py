"""Извлекает детали FAIL-документов из лога baseline (UTF-8 → UTF-8 файл)."""
data = open("bench_qwen3-vl_8b-instruct.log", "rb").read()
text = data.decode("utf-8", "replace")
lines = text.splitlines()

out = []
targets = ["sample_001.pdf", "sample_004.pdf", "sample_011.pdf", "sample_013.pdf"]
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

# Сводка
out.append(f"\n{'='*70}")
out.append("СВОДКА:")
in_summary = False
for line in lines:
    if "СВОДКА" in line or "ИТОГОВАЯ" in line:
        in_summary = True
    if in_summary:
        out.append(line)
        if not line.strip() and in_summary and "Документов" in "\n".join(out[-5:]):
            break

with open("bench_fail_details_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"Записано {len(out)} строк")
