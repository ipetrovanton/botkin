"""Выводит FAIL-документы baseline qwen3-vl:8b-instruct для анализа."""
import json

data = json.load(open("bench_models_results.json", "r", encoding="utf-8"))
qwen = [r for r in data["results"] if r["model"] == "qwen3-vl:8b-instruct"][0]
fails = [d for d in qwen["docs"] if d["status"] == "FAIL"]
print(f"FAIL документов: {len(fails)} из {len(qwen['docs'])}")
print()
for d in fails:
    vals = f"{d['matched']}/{d['expected']}" if d["expected"] else "—"
    print(f"  {d['name']:<24} cls={d['classify_s']:>6.1f}s "
          f"ext={d['extract_s']:>6.1f}s tot={d['total_s']:>6.1f}s vals={vals}")
print()
# Также покажем PASS-документы с экстремальным временем
print("Самые медленные PASS-документы:")
passed = sorted([d for d in qwen["docs"] if d["status"] == "PASS"],
                key=lambda x: x["total_s"], reverse=True)[:5]
for d in passed:
    vals = f"{d['matched']}/{d['expected']}" if d["expected"] else "—"
    print(f"  {d['name']:<24} ext={d['extract_s']:>6.1f}s tot={d['total_s']:>6.1f}s vals={vals}")
