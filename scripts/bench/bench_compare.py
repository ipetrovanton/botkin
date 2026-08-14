"""Парсит результаты оптимизированного прогона из лога."""
import json
import re
from pathlib import Path

log_path = Path(__file__).resolve().parent.parent.parent / "bench_qwen3-vl_8b-instruct.log"
data = log_path.read_bytes()
text = data.decode("utf-8", "replace")
lines = text.splitlines()

# Парсим [E2E] блоки (до строки "ИТОГОВАЯ СВОДКА" — дальше идёт повтор в таблице)
docs = []
current = None
for line in lines:
    if "ИТОГОВАЯ СВОДКА" in line:
        break
    m = re.match(r"\[E2E\]\s+(\S+\.\S+)\s+—\s+(PASS|FAIL|SKIP)", line)
    if m:
        if current:
            docs.append(current)
        current = {"name": m.group(1), "status": m.group(2), "classify_s": 0,
                   "extract_s": 0, "total_s": 0, "matched": 0, "expected": 0}
    elif current:
        m2 = re.search(r"classify\s+([\d.]+)s.*extract\s+([\d.]+)s.*всего\s+([\d.]+)s", line)
        if m2:
            current["classify_s"] = float(m2.group(1))
            current["extract_s"] = float(m2.group(2))
            current["total_s"] = float(m2.group(3))
        m3 = re.search(r"совпало\s+(\d+)/(\d+)", line)
        if m3:
            current["matched"] = int(m3.group(1))
            current["expected"] = int(m3.group(2))
if current:
    docs.append(current)

passed = sum(1 for d in docs if d["status"] == "PASS")
failed = sum(1 for d in docs if d["status"] == "FAIL")
total_matched = sum(d["matched"] for d in docs)
total_expected = sum(d["expected"] for d in docs)
total_s = sum(d["total_s"] for d in docs)

# Сводка из лога
wall_s = 0
for line in reversed(lines):
    m = re.search(r"(\d+\.\d+)s.*\((\d+):(\d+):(\d+)\)", line)
    if m:
        wall_s = float(m.group(1))
        break

# Сравнение с baseline
results_path = Path(__file__).resolve().parent / "bench_models_results.json"
existing = json.load(results_path.open("r", encoding="utf-8"))
baseline = [r for r in existing["results"] if r["model"] == "qwen3-vl:8b-instruct"][0]

result = {
    "model": "qwen3-vl:8b-instruct-optimized",
    "passed": passed, "failed": failed, "skipped": 0,
    "total_matched": total_matched, "total_expected": total_expected,
    "total_s": total_s, "wall_s": wall_s,
    "accuracy": total_matched / total_expected if total_expected else 0,
    "pass_rate": passed / len(docs) if docs else 0,
    "avg_time_per_doc": total_s / len(docs) if docs else 0,
    "score": (total_matched / total_expected if total_expected else 0) *
             (passed / len(docs) if docs else 0) /
             (total_s / len(docs) if docs else 1),
    "error": None, "docs": docs,
}

print("ОПТИМИЗИРОВАННЫЙ ПРОГОН:")
print(f"  PASS: {passed}, FAIL: {failed}, документов: {len(docs)}")
print(f"  Точность: {total_matched}/{total_expected} ({total_matched/total_expected:.1%})")
print(f"  Время: {total_s:.1f}s, среднее: {total_s/len(docs):.1f}s/док")
print(f"  Score: {result['score']:.5f}")
print()
print("BASELINE:")
print(f"  PASS: {baseline['passed']}, FAIL: {baseline['failed']}")
print(f"  Точность: {baseline['total_matched']}/{baseline['total_expected']} ({baseline['accuracy']:.1%})")
print(f"  Время: {baseline['total_s']:.1f}s, среднее: {baseline['avg_time_per_doc']:.1f}s/док")
print(f"  Score: {baseline['score']:.5f}")
print()

# Детализация FAIL
fails = [d for d in docs if d["status"] == "FAIL"]
if fails:
    print(f"FAIL документы ({len(fails)}):")
    for d in fails:
        vals = f"{d['matched']}/{d['expected']}" if d["expected"] else "—"
        print(f"  {d['name']:<24} cls={d['classify_s']:.1f}s ext={d['extract_s']:.1f}s vals={vals}")
else:
    print("FAIL документов НЕТ!")

# Сравнение по документам
print("\nПОДОКУМЕНТНОЕ СРАВНЕНИЕ (только изменения):")
baseline_docs = {d["name"]: d for d in baseline["docs"]}
for d in docs:
    b = baseline_docs.get(d["name"])
    if b and (b["status"] != d["status"] or abs(b["total_s"] - d["total_s"]) > 30):
        vals = f"{d['matched']}/{d['expected']}" if d["expected"] else "—"
        bvals = f"{b['matched']}/{b['expected']}" if b["expected"] else "—"
        delta = d["total_s"] - b["total_s"]
        print(f"  {d['name']:<24} {b['status']}→{d['status']} "
              f"vals {bvals}→{vals} "
              f"time {b['total_s']:.1f}→{d['total_s']:.1f}s ({delta:+.1f}s)")

# Сохраняем
existing["results"] = [r for r in existing["results"]
                       if r["model"] != "qwen3-vl:8b-instruct-optimized"]
existing["results"].append(result)
with results_path.open("w", encoding="utf-8") as f:
    json.dump(existing, f, ensure_ascii=False, indent=2)
