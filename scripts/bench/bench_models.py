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

# Модели по умолчанию для сравнения — актуальные в локальном Ollama.
DEFAULT_MODELS = [
    "huihui_ai/Qwen3.6-abliterated:27b",
    "huihui_ai/Qwen3.6-abliterated:35b",
    "dhiltgen/qwen3-vl:30b-a3b-thinking",
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
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"


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
    precision: float = 0.0
    recall: float = 0.0
    tps: float = 0.0


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
    vram_gb: float = 0.0

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
    def precision(self) -> float:
        """Средняя precision по документам (matched / extracted)."""
        if not self.docs:
            return 0.0
        return sum(d.precision for d in self.docs) / self.num_docs

    @property
    def recall(self) -> float:
        """Средняя recall по документам (matched / expected)."""
        if not self.docs:
            return 0.0
        return sum(d.recall for d in self.docs) / self.num_docs

    @property
    def avg_tps(self) -> float:
        """Средний tps (tokens/second) по документам с ненулевым tps."""
        values = [d.tps for d in self.docs if d.tps > 0]
        return sum(values) / len(values) if values else 0.0

    @property
    def median_time_per_doc(self) -> float:
        if not self.docs:
            return 0.0
        return sorted(d.total_s for d in self.docs)[len(self.docs) // 2]

    @property
    def score(self) -> float:
        """Средневзвешенный score: precision × pass_rate / среднее время.

        Выше = лучше. Модель, которая точнее, стабильнее и быстрее — лидирует.
        """
        if self.avg_time_per_doc <= 0:
            return 0.0
        return (self.precision * self.pass_rate) / self.avg_time_per_doc


def _vram_gb_for_model(model: str) -> float:
    """VRAM (GB) для модели из вывода `ollama ps`. 0.0, если не удалось."""
    try:
        proc = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True,
            timeout=10, env=os.environ.copy(),
        )
        for line in proc.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if not parts:
                continue
            if parts[0] == model:
                # SIZE колонка: "8.0 GB" -> 8.0
                size_idx = parts.index("GB") - 1 if "GB" in parts else 1
                if size_idx >= 0 and parts[size_idx].replace(".", "", 1).isdigit():
                    return float(parts[size_idx])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return 0.0


def _to_float(raw: str) -> float:
    """Парсинг ячейки precision/recall (— → 0.0)."""
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _parse_tps_per_doc(output: str) -> dict[str, float]:
    """Извлекает tps=... из E2E-блоков по каждому документу (среднее по всем вызовам)."""
    tps_by_doc: dict[str, float] = {}
    current_doc: str | None = None
    current_values: list[float] = []
    tps_re = re.compile(r"tps\s*=\s*([\d.]+)")

    def _flush() -> None:
        nonlocal current_doc
        if current_doc and current_values:
            tps_by_doc[current_doc] = sum(current_values) / len(current_values)
        current_doc = None
        current_values.clear()

    for line in output.splitlines():
        if "[E2E]" in line:
            _flush()
            parts = line.split()
            for part in parts:
                if "." in part and not part.endswith(":"):
                    current_doc = part
                    break
            continue
        if line.startswith("=") or line.startswith("#"):
            _flush()
            continue
        m = tps_re.search(line)
        if m and current_doc is not None:
            current_values.append(float(m.group(1)))
    _flush()
    return tps_by_doc


def parse_pytest_summary(output: str) -> list[DocResult]:
    """Извлекает строки документов из секции «ИТОГОВАЯ СВОДКА E2E».

    Формат сводки:
    sample_001.pdf  PASS   10.5s   38.0s  48.5s      1.00     1.00        0
    """
    tps_by_doc = _parse_tps_per_doc(output)
    docs: list[DocResult] = []
    in_summary = False
    row_re = re.compile(
        r"^(\S+\.\S+)\s+(PASS|FAIL|SKIP)\s+([\d.]+)s\s+([\d.]+)s\s+([\d.]+)s\s+(\S+)\s+(\S+)\s+(\S+)"
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
                name, status, cls_s, ext_s, tot_s, precision_s, recall_s, _ = m.groups()
                docs.append(DocResult(
                    name=name, status=status,
                    classify_s=float(cls_s), extract_s=float(ext_s),
                    total_s=float(tot_s),
                    matched=0, expected=0,
                    precision=_to_float(precision_s),
                    recall=_to_float(recall_s),
                    tps=tps_by_doc.get(name, 0.0),
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
    # TEXT_MODEL оставляем по умолчанию: так сравнивается именно VLM/OCR-первая
    # ступень, а второй этап (структурирование) идёт на штатной текстовой модели.
    # env["TEXT_MODEL"] = model
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
    text_model = os.environ.get("TEXT_MODEL", "<default>")
    print(f"[BENCH] VLM_MODEL={model} TEXT_MODEL={text_model}")
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

        result.vram_gb = _vram_gb_for_model(model)
        print(f"\n[BENCH] {model}: PASS={result.passed} FAIL={result.failed} "
              f"SKIP={result.skipped} | точность={result.total_matched}/{result.total_expected} "
              f"({result.accuracy:.1%}) | среднее={result.avg_time_per_doc:.1f}s/док "
              f"| median={result.median_time_per_doc:.1f}s | vram={result.vram_gb:.1f}GB "
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
    print(f"\n\n{'#' * 100}")
    print("СРАВНИТЕЛЬНАЯ ТАБЛИЦА МОДЕЛЕЙ")
    print("#" * 100)
    header = (
        f"{'Модель':<26}{'PASS':>6}{'FAIL':>6}{'SKIP':>6}"
        f"{'precision':>10}{'recall':>8}{'pass%':>7}{'tps':>8}{'ср.время':>10}{'score':>10}{'wall':>8}"
    )
    print(header)
    print("-" * 100)
    for r in sorted(results, key=lambda x: x.score, reverse=True):
        precision = f"{r.precision:.2%}" if r.num_docs else "—"
        recall = f"{r.recall:.2%}" if r.num_docs else "—"
        tps = f"{r.avg_tps:.1f}" if r.avg_tps > 0 else "—"
        print(
            f"{r.model:<26}{r.passed:>6}{r.failed:>6}{r.skipped:>6}"
            f"{precision:>10}{recall:>8}{r.pass_rate:>6.0%}{tps:>8}"
            f"{r.avg_time_per_doc:>9.1f}s{r.score:>10.4f}{r.wall_s:>7.0f}s"
        )
    print("-" * 100)
    print("score = (precision × pass_rate) / среднее_время_на_документ — выше = лучше")
    print("#" * 100)


def save_markdown(results: list[ModelResult]) -> Path:
    """Сохраняет сравнительную таблицу в benchmarks/models_comparison_YYYY-MM-DD.md."""
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    path = BENCHMARKS_DIR / f"models_comparison_{time.strftime('%Y-%m-%d')}.md"
    lines = [
        "# Сравнение VLM-моделей",
        "",
        f"Дата: {time.strftime('%Y-%m-%d %H:%M')}",
        f"Документов: {results[0].num_docs if results else 0}",
        "",
        "| Модель | PASS | FAIL | SKIP | precision | recall | median, s | avg, s | tps | vram, GB |",
        "|--------|------|------|------|-----------|--------|-----------|--------|-----|----------|",
    ]
    for r in sorted(results, key=lambda x: x.score, reverse=True):
        precision = f"{r.precision:.2%}" if r.num_docs else "—"
        recall = f"{r.recall:.2%}" if r.num_docs else "—"
        tps = f"{r.avg_tps:.1f}" if r.avg_tps > 0 else "—"
        lines.append(
            f"| {r.model} | {r.passed} | {r.failed} | {r.skipped} | "
            f"{precision} | {recall} | {r.median_time_per_doc:.1f} | "
            f"{r.avg_time_per_doc:.1f} | {tps} | {r.vram_gb:.1f} |"
        )
    lines += ["", "*score = (precision × pass_rate) / avg_time*"]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[BENCH] Markdown сохранён: {path}", flush=True)
    return path


def _reparse_logs() -> None:
    """Пересобирает JSON/MD-отчёт из bench_*.log, оставляя из JSON vram/wall."""
    results: list[ModelResult] = []
    legacy: dict[str, dict] = {}
    if RESULTS_FILE.exists():
        with RESULTS_FILE.open(encoding="utf-8") as f:
            legacy = {m["model"]: m for m in json.load(f).get("results", [])}

    for log_file in sorted(PROJECT_ROOT.glob("bench_*.log")):
        # Имя файла: bench_<model>.log; двоеточие и слэш заменялись на _.
        stem = log_file.stem[6:]  # без "bench_"
        # Восстанавливаем : и / невозможно однозначно, берём model из legacy по имени файла.
        model = None
        for m in legacy:
            if m.replace(":", "_").replace("/", "_") == stem:
                model = m
                break
        if model is None:
            # Если в JSON нет — попробуем угадать по наличию в DEFAULT_MODELS.
            for m in ALL_KNOWN_MODELS:
                if m.replace(":", "_").replace("/", "_") == stem:
                    model = m
                    break
        if model is None:
            continue

        output = log_file.read_text(encoding="utf-8", errors="replace")
        docs = parse_pytest_summary(output)
        if not docs:
            continue

        mr = ModelResult(model=model)
        mr.docs = docs
        mr.total_classify_s = sum(d.classify_s for d in docs)
        mr.total_extract_s = sum(d.extract_s for d in docs)
        mr.total_s = sum(d.total_s for d in docs)
        mr.passed = sum(1 for d in docs if d.status == "PASS")
        mr.failed = sum(1 for d in docs if d.status == "FAIL")
        mr.skipped = sum(1 for d in docs if d.status == "SKIP")
        # wall/vram/error сохраняем из старого JSON, если он есть.
        legacy_entry = legacy.get(model, {})
        mr.wall_s = legacy_entry.get("wall_s", 0.0)
        mr.vram_gb = legacy_entry.get("vram_gb", 0.0)
        mr.error = legacy_entry.get("error")
        mr.raw_output = output
        results.append(mr)
        print(f"[BENCH] Переспарсили: {model} | docs={len(docs)} | PASS={mr.passed}")

    if not results:
        print("[BENCH] Нет подходящих bench_*.log для пересборки.")
        return

    _save_results(results)
    print_comparison(results)
    save_markdown(results)


def _save_results(results: list[ModelResult]) -> None:
    """Сохраняет результаты в JSON."""
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
            "vram_gb": mr.vram_gb,
            "accuracy": mr.accuracy,
            "pass_rate": mr.pass_rate,
            "avg_time_per_doc": mr.avg_time_per_doc,
            "score": mr.score,
            "error": mr.error,
            "docs": [
                {"name": d.name, "status": d.status,
                 "classify_s": d.classify_s, "extract_s": d.extract_s,
                 "total_s": d.total_s, "matched": d.matched, "expected": d.expected,
                 "precision": d.precision, "recall": d.recall}
                for d in mr.docs
            ],
            "precision": mr.precision,
            "recall": mr.recall,
            "avg_tps": mr.avg_tps,
        })
    with RESULTS_FILE.open("w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"[BENCH] Результаты сохранены в {RESULTS_FILE}", flush=True)


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
    parser.add_argument("--reparse", action="store_true",
                        help="Пересобрать отчёт из сохранённых bench_*.log без повторного запуска pytest")
    args = parser.parse_args()

    if args.reparse:
        _reparse_logs()
        return

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
        _save_results(results)

    print_comparison(results)
    save_markdown(results)


if __name__ == "__main__":
    main()
