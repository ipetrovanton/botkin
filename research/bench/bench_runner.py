"""Прогон e2e N раз с замером времени и качества."""
import subprocess, re, sys

N = 3
results = []
for i in range(N):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_e2e_llm.py",
         "-m", "llm", "-s", "--tb=line", "-k", "sample_001"],
        capture_output=True, text=True, timeout=600, cwd=".",
    )
    out = r.stdout + r.stderr
    time_match = re.search(r"extract\s+([\d.]+)s", out)
    vals_match = re.search(r"(\d+)/3", out)
    t = time_match.group(1) if time_match else "?"
    v = vals_match.group(1) if vals_match else "?"
    s = "PASS" if "PASS" in out and "FAIL" not in out else "FAIL"
    results.append((i + 1, t, s, v))
    print(f"Run {i+1}: extract={t}s | {s} | {v}/3", flush=True)

passes = sum(1 for _, _, s, _ in results if s == "PASS")
print(f"\nTotal: {passes}/{N} PASS")
