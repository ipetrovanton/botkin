"""Сравнительный бенчмарк medical reasoning на нескольких uncensored LLM.

Для каждой модели:
  1. Устанавливает REASONING_MODEL через env
  2. Запускает pytest tests/test_e2e_reasoning.py -m reasoning -s
  3. Парсит результаты (PASS/FAIL, тайминги, ключевые проверки)
  4. Сохраняет результат в JSON

В конце выводит сравнительную таблицу.

Запуск:
  uv run python bench_reasoning.py
  uv run python bench_reasoning.py --models huihui_ai/Qwen3.6-abliterated:27b
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_FILE = Path(__file__).resolve().parent / "bench_reasoning_results.json"

DEFAULT_MODELS = [
    "huihui_ai/Qwen3.6-abliterated:27b",
    "lukey03/qwen3.5-9b-abliterated-vision:latest",
    "satgeze/qwen36-35b-uncensored-1m",
    "goekdenizguelmez/JOSIEFIED-Qwen3:8b-health",
    "OussamaELALLAM/MedExpert",
    "huihui_ai/Qwen3.6-abliterated:35b",
    "huihui_ai/glm-4.7-flash-abliterated",
    "alibilge/Huihui-GLM-4.6V-Flash-abliterated:q4_k_m",
    "gemma4:latest",
]

# GLM-4.7-Flash — reasoning MoE; GLM-4.6V-Flash — компактный VLM без обязательного thinking.
MODEL_OPTIONS: dict[str, dict[str, str]] = {
    "huihui_ai/glm-4.7-flash-abliterated": {
        "REASONING_NUM_PREDICT": "8192",
        "REASONING_THINK": "medium",
    },
    "alibilge/Huihui-GLM-4.6V-Flash-abliterated:q4_k_m": {
        "REASONING_NUM_PREDICT": "4096",
        "REASONING_THINK": "false",
    },
}

ALL_KNOWN_MODELS = DEFAULT_MODELS + [
    "bge-m3",
    "qwen3:8b",
    "qwen3-vl:8b-instruct",
    "minicpm-v:8b",
    "qwen2.5vl:7b",
    "glm-ocr:latest",
]


@dataclass
class TestResult:
    """Результат одного теста."""
    name: str
    status: str  # PASS | FAIL | SKIP
    time_s: float = 0.0
    output_snippet: str = ""


@dataclass
class ModelResult:
    """Полный результат прогона одной модели."""
    model: str
    tests: list[TestResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_time_s: float = 0.0
    wall_s: float = 0.0
    raw_output: str = ""
    error: str | None = None

    @property
    def num_tests(self) -> int:
        return len(self.tests)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.num_tests if self.num_tests else 0.0

    @property
    def avg_time_per_test(self) -> float:
        return self.total_time_s / self.passed if self.passed else 0.0

    @property
    def score(self) -> float:
        """Выше = лучше. pass_rate / avg_time."""
        if self.avg_time_per_test <= 0:
            return 0.0
        return self.pass_rate / self.avg_time_per_test


def parse_pytest_output(output: str) -> list[TestResult]:
    """Извлекает результаты тестов из вывода pytest -s."""
    tests: list[TestResult] = []
    # Ищем строки [SPEED] для извлечения времён
    speed_re = re.compile(r"\[SPEED\]\s+(.+?):\s+([\d.]+)s.*")
    # Ищем имена тестов и статусы
    patterns = [
        re.compile(
            r"(?P<status>PASSED|FAILED|SKIPPED|ERROR)\s+.*?"
            r"tests/test_e2e_reasoning\.py::(?P<class_name>[^:]+)::(?P<test_name>[^\s]+)"
        ),
        re.compile(
            r"tests/test_e2e_reasoning\.py::(?P<class_name>[^:]+)::(?P<test_name>[^\s]+)\s+"
            r"(?P<status>PASSED|FAILED|SKIPPED|ERROR)"
        ),
    ]
    for line in output.splitlines():
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                status = match.group("status")
                tests.append(TestResult(
                    name=f"{match.group('class_name')}::{match.group('test_name')}",
                    status=status.replace("PASSED", "PASS").replace("FAILED", "FAIL")
                    .replace("SKIPPED", "SKIP").replace("ERROR", "FAIL"),
                ))
                break

    # Достаём времена из [SPEED] строк
    speed_map: dict[str, float] = {}
    for line in output.splitlines():
        m = speed_re.search(line)
        if m:
            test_label = m.group(1).strip()
            elapsed = float(m.group(2))
            speed_map[test_label] = elapsed

    # Сопоставляем времена с тестами (по подстроке)
    for tr in tests:
        for label, elapsed in speed_map.items():
            if label.lower() in tr.name.lower() or tr.name.lower() in label.lower():
                tr.time_s = elapsed
                break

    return tests


def run_model(model: str, timeout: int = 3600) -> ModelResult:
    """Прогоняет reasoning-тесты на одной модели."""
    result = ModelResult(model=model)

    # Выгружаем все модели
    for m in ALL_KNOWN_MODELS:
        if m == model:
            continue
        try:
            subprocess.run(["ollama", "stop", m], timeout=30,
                         capture_output=True, check=False)
        except subprocess.TimeoutExpired:
            pass

    env = {
        **dict(__import__("os").environ),
        "REASONING_MODEL": model,
        **MODEL_OPTIONS.get(model, {}),
    }

    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_e2e_reasoning.py",
        "-m", "reasoning",
        "-s", "--tb=short", "-vv",
        "--no-header",
    ]

    print(f"\n{'='*70}")
    print(f"[BENCH-REASONING] Модель: {model}")
    print(f"[BENCH-REASONING] Команда: {' '.join(cmd)}")
    print(f"[BENCH-REASONING] REASONING_MODEL={model}")
    print(f"{'='*70}\n", flush=True)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        result.raw_output = proc.stdout + "\n" + proc.stderr
        result.wall_s = time.monotonic() - start
    except subprocess.TimeoutExpired:
        result.error = f"Таймаут {timeout}s"
        result.wall_s = time.monotonic() - start
        return result
    except Exception as e:
        result.error = str(e)
        result.wall_s = time.monotonic() - start
        return result

    # Парсим результаты
    result.tests = parse_pytest_output(result.raw_output)
    for t in result.tests:
        if t.status == "PASS":
            result.passed += 1
            result.total_time_s += t.time_s
        elif t.status == "FAIL":
            result.failed += 1
        else:
            result.skipped += 1

    # Если тесты не распарсились, пробуем из summary
    if not result.tests:
        summary_re = re.compile(r"(\d+) passed.*?(\d+) failed.*?(\d+) skipped")
        m = summary_re.search(result.raw_output)
        if m:
            result.passed = int(m.group(1))
            result.failed = int(m.group(2))
            result.skipped = int(m.group(3))

    return result


def print_comparison(results: list[ModelResult]) -> None:
    """Выводит сравнительную таблицу."""
    print(f"\n{'='*90}")
    print("СРАВНИТЕЛЬНАЯ ТАБЛИЦА REASONING-МОДЕЛЕЙ")
    print(f"{'='*90}")
    header = f"{'Модель':<45} {'PASS':>5} {'FAIL':>5} {'SKIP':>5} {'Pass%':>6} {'AvgS':>7} {'WallS':>8} {'Score':>8}"
    print(header)
    print("-" * 90)
    for r in sorted(results, key=lambda x: x.score, reverse=True):
        print(
            f"{r.model[:44]:<45} {r.passed:>5} {r.failed:>5} {r.skipped:>5} "
            f"{r.pass_rate*100:>5.0f}% {r.avg_time_per_test:>6.1f}s {r.wall_s:>7.0f}s {r.score:>8.5f}"
        )
    print("=" * 90)


def main() -> int:
    parser = argparse.ArgumentParser(description="Бенчмарк reasoning-моделей")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                       help="Список моделей для тестирования")
    parser.add_argument("--timeout", type=int, default=3600,
                       help="Таймаут на одну модель (секунды)")
    args = parser.parse_args()

    results: list[ModelResult] = []
    for model in args.models:
        r = run_model(model, timeout=args.timeout)
        results.append(r)
        print(f"\n[RESULT] {model}: {r.passed}P/{r.failed}F/{r.skipped}S, "
              f"wall={r.wall_s:.0f}s, score={r.score:.5f}", flush=True)

    print_comparison(results)

    # Сохраняем в JSON
    data = []
    for r in results:
        data.append({
            "model": r.model,
            "passed": r.passed,
            "failed": r.failed,
            "skipped": r.skipped,
            "total_time_s": round(r.total_time_s, 1),
            "wall_s": round(r.wall_s, 1),
            "pass_rate": round(r.pass_rate, 3),
            "avg_time_per_test": round(r.avg_time_per_test, 1),
            "score": round(r.score, 5),
            "error": r.error,
            "tests": [
                {"name": t.name, "status": t.status, "time_s": round(t.time_s, 1)}
                for t in r.tests
            ],
        })
    RESULTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\nРезультаты сохранены: {RESULTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
