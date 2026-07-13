"""Выводит сравнительную таблицу результатов бенчмарка."""
import json

data = json.load(open("bench_models_results.json", "r", encoding="utf-8"))
results = data["results"]

header = f"{'Модель':<26}{'PASS':>6}{'FAIL':>6}{'точность':>10}{'pass%':>7}{'ср.время':>10}{'score':>10}"
print(header)
print("-" * 80)
for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True):
    acc = f"{r['total_matched']}/{r['total_expected']}" if r["total_expected"] else "—"
    avg = r.get("avg_time_per_doc", 0)
    score = r.get("score", 0)
    pass_rate = r.get("pass_rate", 0)
    print(f"{r['model']:<26}{r['passed']:>6}{r['failed']:>6}{acc:>10}{pass_rate:>6.0%}{avg:>9.1f}s{score:>10.5f}")
print("-" * 80)
print("score = (accuracy x pass_rate) / avg_time -- higher = better")
print()

# Детализация по документам
for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True):
    if not r.get("docs"):
        continue
    print(f"\n--- {r['model']} ---")
    for d in sorted(r["docs"], key=lambda x: x["name"]):
        vals = f"{d['matched']}/{d['expected']}" if d["expected"] else "—"
        print(f"  {d['name']:<24} {d['status']:<5} cls={d['classify_s']:>6.1f}s "
              f"ext={d['extract_s']:>6.1f}s tot={d['total_s']:>6.1f}s vals={vals}")
    if r.get("error"):
        print(f"  ПРИМЕЧАНИЕ: {r['error']}")
