"""Сравнительный бенчмарк e2e на нескольких VLM-моделях.

Для каждой модели:
  1. Устанавливает VLM_MODEL и TEXT_MODEL через env
  2. Запускает pytest tests/test_e2e_llm.py -m llm -s
  3. Парсит итоговую сводку (тайминги, точность, PASS/FAIL)
  4. Сохраняет результат в JSON

В конце выводит сравнительную таблицу со средневзвешенным score
(точность / среднее время на документ — выше = лучше).

Запуск:
  uv run python bench_models.py
  uv run python bench_models.py --models qwen3-vl:8b-instruct gemma4:latest
  uv run python bench_models.py --skip-synthetic
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Модели по умолчанию для сравнения.
DEFAULT_MODELS = [
    "qwen3-vl:8b-instruct",
    "gemma4:latest",
    "minicpm-v:8b",
    "qwen3-vl:30b-a3b",
    "qwen2.5vl:7b",
    "glm-ocr:latest",
    "qwen3.5:9b",
    "haervwe/GLM-4.6V-Flash-9B",
]

# Все модели, которые нужно выгружать перед запуском очередной —
# включая RAG-модели (bge-m3, qwen3:8b), чтобы освободить память.
ALL_KNOWN_MODELS = DEFAULT_MODELS + [
    "bge-m3",
    "qwen3:8b",
    "paddleocr-vl16:latest",
    "MedAIBase/MedGemma1.5:4b-it",
    "puyangwang/medgemma-27b-it:q4_k_m",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_FILE = Path(__file__).resolve().parent / "bench_models_results.json"


@dataclass
class DocResult:
    """Результат одного документа из итоговой сводки pytest."""
    name: str
    status: str  # PASS | FAIL | SKIP
    classify_s: float = 0.0
    extract_s: float = 0.0
    total_s: float = 0.0
    matched: int = 0
    expected: int = 0


@dataclass
class ModelResult:
    """Полный результат прогона одной модели."""
    model: str
    docs: list[DocResult] = field(default_factory=list)
    total_classify_s: float = 0.0
    total_extract_s: float = 0.0
    total_s: float = 0.0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_matched: int = 0
    total_expected: int = 0
    raw_output: str = ""
    error: str | None = None
    wall_s: float = 0.0

    @property
    def num_docs(self) -> int:
        return len(self.docs)

    @property
    def avg_time_per_doc(self) -> float:
        return self.total_s / self.num_docs if self.num_docs else 0.0

    @property
    def accuracy(self) -> float:
        """Доля совпавших эталонных значений (0.0 — 1.0)."""
        return self.total_matched / self.total_expected if self.total_expected else 0.0

    @property
    def pass_rate(self) -> float:
        """Документов PASS / всего документов."""
        return self.passed / self.num_docs if self.num_docs else 0.0

    @property
    def score(self) -> float:
        """Средневзвешенный score: точность × pass_rate / среднее время.

        Выше = лучше. Модель, которая точнее, стабильнее и быстрее — лидирует.
        """
        if self.avg_time_per_doc <= 0:
            return 0.0
        return (self.accuracy * self.pass_rate) / self.avg_time_per_doc


def parse_pytest_summary(output: str) -> list[DocResult]:
    """Извлекает строки документов из секции «ИТОГОВАЯ СВОДКА E2E»."""
    docs: list[DocResult] = []
    in_summary = False
    # Строка: sample_001.pdf  PASS   10.5s   38.0s  48.5s    3/3
    row_re = re.compile(
        r"^(\S+\.\S+)\s+(PASS|FAIL|SKIP)\s+([\d.]+)s\s+([\d.]+)s\s+([\d.]+)s\s+(\S+)"
    )
    for line in output.splitlines():
        if "ИТОГОВАЯ СВОДКА E2E" in line:
            in_summary = True
            continue
        if in_summary:
            if line.startswith("---") or line.startswith("#"):
                continue
            if line.startswith("Документов:") or line.startswith("Время:"):
                continue
            if line.startswith("Среднее"):
                continue
            if line.startswith("ПРОВАЛЫ"):
                break
            m = row_re.match(line.strip())
            if m:
                name, status, cls_s, ext_s, tot_s, vals = m.groups()
                matched, expected = 0, 0
                if "/" in vals and vals != "—":
                    parts = vals.split("/")
                    try:
                        matched = int(parts[0])
                        expected = int(parts[1])
                    except ValueError:
                        pass
                docs.append(DocResult(
                    name=name, status=status,
                    classify_s=float(cls_s), extract_s=float(ext_s),
                    total_s=float(tot_s),
                    matched=matched, expected=expected,
                ))
    return docs


def run_model(model: str, skip_synthetic: bool = False, timeout: int = 7200) -> ModelResult:
    """Прогоняет e2e-тесты на одной модели и возвращает результат."""
    result = ModelResult(model=model)

    # Выгружаем все загруженные модели, чтобы освободить память — иначе новая
    # модель может не поместиться и уйдёт в оффлоад (10-100x медленнее).
    for m in ALL_KNOWN_MODELS:
        try:
            subprocess.run(["ollama", "stop", m], timeout=30,
                           capture_output=True, env=os.environ.copy())
        except (OSError, subprocess.SubprocessError):
            pass
    # Пауза для освобождения VRAM.
    time.sleep(5)

    env = os.environ.copy()
    env["VLM_MODEL"] = model
    env["TEXT_MODEL"] = model
    # Принудительно localhost — нативная Windows Ollama (модуль читает OLLAMA_URL).
    env["OLLAMA_URL"] = "http://localhost:11434"
    # pytest на Windows пишет stdout в cp1251; форсируем UTF-8 для корректного
    # парсинга кириллицы в итоговой сводке (надстрочные 10⁹/л, единицы измерения).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_e2e_llm.py",
        "-m", "llm",
        "-s",
        "--tb=line",
        "-q",
    ]
    if skip_synthetic:
        cmd += ["-k", "real_document"]

    print(f"\n{'=' * 70}")
    print(f"[BENCH] Модель: {model}")
    print(f"[BENCH] Команда: {' '.join(cmd)}")
    print(f"[BENCH] VLM_MODEL={model} TEXT_MODEL={model}")
    print(f"{'=' * 70}", flush=True)

    # Вывод pytest перенаправляем в файл — capture_output на Windows падает
    # на unicode-символах (надстрочные 10⁹/л в cp1251-консоли).
    log_file = PROJECT_ROOT / f"bench_{model.replace(':', '_').replace('/', '_')}.log"
    t0 = time.perf_counter()
    try:
        with log_file.open("w", encoding="utf-8", errors="replace") as logf:
            proc = subprocess.run(
                cmd, stdout=logf, stderr=subprocess.STDOUT,
                timeout=timeout, cwd=str(PROJECT_ROOT),
                env=env,
            )
        result.wall_s = time.perf_counter() - t0
        output = log_file.read_text(encoding="utf-8", errors="replace")
        result.raw_output = output

        if proc.returncode != 0 and not output:
            result.error = f"pytest exit={proc.returncode}, пустой вывод"
            return result

        # Парсим документы из сводки.
        result.docs = parse_pytest_summary(output)

        # Агрегаты.
        for d in result.docs:
            result.total_classify_s += d.classify_s
            result.total_extract_s += d.extract_s
            result.total_s += d.total_s
            if d.status == "PASS":
                result.passed += 1
            elif d.status == "FAIL":
                result.failed += 1
            else:
                result.skipped += 1
            result.total_matched += d.matched
            result.total_expected += d.expected

        # Если сводка не распарсилась — пробуем извлечь из [SPEED] строк.
        if not result.docs:
            speed_re = re.compile(r"\[SPEED\]\s+(?:classify|extract)\s+\(([^)]+)\):\s+([\d.]+)s")
            classify_s = 0.0
            extract_s = 0.0
            for m in speed_re.finditer(output):
                stage, secs = m.groups()
                if "classify" in stage:
                    classify_s += float(secs)
                else:
                    extract_s += float(secs)
            if classify_s or extract_s:
                result.total_classify_s = classify_s
                result.total_extract_s = extract_s
                result.total_s = classify_s + extract_s
                result.error = "сводка не распарсена, тайминги из [SPEED]"
            else:
                result.error = "не удалось извлечь результаты"

        print(f"\n[BENCH] {model}: PASS={result.passed} FAIL={result.failed} "
              f"SKIP={result.skipped} | точность={result.total_matched}/{result.total_expected} "
              f"({result.accuracy:.1%}) | среднее={result.avg_time_per_doc:.1f}s/док "
              f"| wall={result.wall_s:.0f}s", flush=True)

    except subprocess.TimeoutExpired:
        result.wall_s = time.perf_counter() - t0
        result.error = f"timeout после {timeout}s"
        print(f"\n[BENCH] {model}: TIMEOUT после {timeout}s", flush=True)
    except Exception as e:
        result.wall_s = time.perf_counter() - t0
        result.error = f"исключение: {e}"
        print(f"\n[BENCH] {model}: ОШИБКА {e}", flush=True)

    return result


def print_comparison(results: list[ModelResult]) -> None:
    """Выводит сравнительную таблицу всех моделей."""
    print(f"\n\n{'#' * 90}")
    print("СРАВНИТЕЛЬНАЯ ТАБЛИЦА МОДЕЛЕЙ")
    print("#" * 90)
    header = (
        f"{'Модель':<26}{'PASS':>6}{'FAIL':>6}{'SKIP':>6}"
        f"{'точность':>10}{'pass%':>7}{'ср.время':>10}{'score':>10}{'wall':>8}"
    )
    print(header)
    print("-" * 90)
    for r in sorted(results, key=lambda x: x.score, reverse=True):
        acc = f"{r.total_matched}/{r.total_expected}" if r.total_expected else "—"
        print(
            f"{r.model:<26}{r.passed:>6}{r.failed:>6}{r.skipped:>6}"
            f"{acc:>10}{r.pass_rate:>6.0%}{r.avg_time_per_doc:>9.1f}s"
            f"{r.score:>10.4f}{r.wall_s:>7.0f}s"
        )
    print("-" * 90)
    print("score = (точность × pass_rate) / среднее_время_на_документ — выше = лучше")
    print("#" * 90)

    # Детализация по документам для каждой модели.
    for r in results:
        if not r.docs:
            continue
        print(f"\n--- {r.model} (детализация) ---")
        for d in sorted(r.docs, key=lambda x: x.name):
            vals = f"{d.matched}/{d.expected}" if d.expected else "—"
            print(f"  {d.name:<24} {d.status:<5} cls={d.classify_s:>6.1f}s "
                  f"ext={d.extract_s:>6.1f}s tot={d.total_s:>6.1f}s vals={vals}")


def main():
    parser = argparse.ArgumentParser(description="Сравнительный бенчмарк VLM-моделей")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help="Список моделей для тестирования")
    parser.add_argument("--skip-synthetic", action="store_true",
                        help="Пропустить синтетический тест ОАК")
    parser.add_argument("--timeout", type=int, default=7200,
                        help="Таймаут на одну модель (секунды)")
    parser.add_argument("--resume", action="store_true",
                        help="Дозагрузить только недостающие модели из результатов")
    args = parser.parse_args()

    results: list[ModelResult] = []
    existing: dict[str, ModelResult] = {}

    if args.resume and RESULTS_FILE.exists():
        with RESULTS_FILE.open(encoding="utf-8") as f:
            saved = json.load(f)
        for m in saved.get("results", []):
            mr = ModelResult(model=m["model"])
            mr.passed = m.get("passed", 0)
            mr.failed = m.get("failed", 0)
            mr.skipped = m.get("skipped", 0)
            mr.total_matched = m.get("total_matched", 0)
            mr.total_expected = m.get("total_expected", 0)
            mr.total_s = m.get("total_s", 0.0)
            mr.wall_s = m.get("wall_s", 0.0)
            mr.error = m.get("error")
            existing[mr.model] = mr
            results.append(mr)
            print(f"[BENCH] Восстановлено из кэша: {mr.model}")

    for model in args.models:
        if model in existing:
            continue
        r = run_model(model, skip_synthetic=args.skip_synthetic, timeout=args.timeout)
        results.append(r)

        # Сохраняем после каждой модели — чтобы не потерять при сбое.
        save_data = {"results": []}
        for mr in results:
            save_data["results"].append({
                "model": mr.model,
                "passed": mr.passed,
                "failed": mr.failed,
                "skipped": mr.skipped,
                "total_matched": mr.total_matched,
                "total_expected": mr.total_expected,
                "total_s": mr.total_s,
                "wall_s": mr.wall_s,
                "accuracy": mr.accuracy,
                "pass_rate": mr.pass_rate,
                "avg_time_per_doc": mr.avg_time_per_doc,
                "score": mr.score,
                "error": mr.error,
                "docs": [
                    {"name": d.name, "status": d.status,
                     "classify_s": d.classify_s, "extract_s": d.extract_s,
                     "total_s": d.total_s, "matched": d.matched, "expected": d.expected}
                    for d in mr.docs
                ],
            })
        with RESULTS_FILE.open("w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        print(f"[BENCH] Результаты сохранены в {RESULTS_FILE}", flush=True)

    print_comparison(results)


if __name__ == "__main__":
    main()
