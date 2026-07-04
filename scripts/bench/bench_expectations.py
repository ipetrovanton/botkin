"""Бенчмарк «ожидания vs реальность»: заявленные скоры моделей против нашего корпуса.

Прогоняет e2e-тесты (tests/test_e2e_llm.py, корпус из sidecar-эталонов
tests/fixtures/documents/samples/) на нескольких моделях и сводит в один отчёт:
что модель ОБЕЩАЕТ по публичным бенчмаркам (OmniDocBench, заявленная скорость,
model card) и что она ПОКАЗЫВАЕТ на реальных русских медицинских документах.

Ожидания собраны в docs/ocr-models-research-2026-07.md (Часть 2) — каждый факт
со ссылкой на первоисточник. Реальность — замер этого скрипта.

Запуск (нужны Ollama + GPU; модели подтянутся с --pull):
    .venv/bin/python scripts/bench/bench_expectations.py
    .venv/bin/python scripts/bench/bench_expectations.py --models qwen3-vl:8b-instruct glm-ocr
    .venv/bin/python scripts/bench/bench_expectations.py --pull --skip-synthetic

Результат:
    scripts/bench/bench_expectations_report.md  — готовая фактура для статьи
    scripts/bench/bench_expectations_results.json — сырые числа
    + наглядная таблица в консоль
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# bench_models лежит рядом и не является пакетом — импортируем по месту.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_models import ModelResult, run_model  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_FILE = Path(__file__).resolve().parent / "bench_expectations_report.md"
RESULTS_FILE = Path(__file__).resolve().parent / "bench_expectations_results.json"

# Модели по умолчанию: текущая боевая, новый кандидат qwen3.5:9b, GLM-4.6V-Flash-9B.
# qwen2.5vl:7b исключена — 76.3% на корпусе, слишком слабая (итерация 33).
DEFAULT_MODELS = [
    "qwen3-vl:8b-instruct",
    "qwen3.5:9b",
    "haervwe/GLM-4.6V-Flash-9B",
]


@dataclass
class Expectation:
    """Публично заявленные показатели модели (с первоисточниками).

    ВАЖНО: числа не выдумываются — только то, что есть в
    docs/ocr-models-research-2026-07.md (Часть 2) с URL. Нет числа — «н/д».
    """
    omnidocbench: str          # скор + ВЕРСИЯ бенча (v1.5/v1.6 несравнимы!)
    claimed_speed: str         # заявленная скорость + на каком GPU
    disk: str                  # веса на диске
    russian: str               # что известно про русский
    sources: list[str]


# Ключ — имя модели Ollama без тега (до ':').
EXPECTATIONS: dict[str, Expectation] = {
    "glm-ocr": Expectation(
        omnidocbench="95.22 (v1.6, официальный лидерборд OpenDataLab)",
        claimed_speed="1.86 стр/с PDF (Z.ai; GPU не указан, конкурентность 1)",
        disk="2.65 ГБ BF16 (1.33B параметров суммарно)",
        russian="ru заявлен в model card (zh/en/fr/es/ru/de/ja/ko); бенчей по кириллице нет",
        sources=[
            "https://github.com/zai-org/GLM-OCR",
            "https://github.com/opendatalab/OmniDocBench",
            "https://docs.z.ai/guides/vlm/glm-ocr",
        ],
    ),
    "qwen3-vl": Expectation(
        omnidocbench="н/д для 8B (235B-версия: 89.78 на v1.6)",
        claimed_speed="н/д",
        disk="17.5 ГБ BF16 / ~5–6 ГБ Q4 (8.77B параметров)",
        russian="OCR на 32 языках заявлен (кириллица входит); "
                "olmOCR-bench 64.6±1.1 [независимый замер Datalab]",
        sources=[
            "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct",
            "https://huggingface.co/datalab-to/chandra-ocr-2",
            "https://github.com/QwenLM/Qwen3-VL",
        ],
    ),
    "qwen2.5vl": Expectation(
        omnidocbench="v1.0: overall edit 0.226 EN / 0.324 ZH (tech report; шкала иная, не сравнивать с v1.5+)",
        claimed_speed="н/д",
        disk="16.6 ГБ BF16 (8.29B параметров)",
        russian="мультиязычный OCR заявлен, ru без цифр",
        sources=["https://arxiv.org/abs/2502.13923"],
    ),
    "minicpm-v": Expectation(
        omnidocbench="н/д (в исследованных лидербордах отсутствует)",
        claimed_speed="н/д",
        disk="~5.5 ГБ (8B, Q4 в Ollama)",
        russian="н/д",
        sources=["https://ollama.com/library/minicpm-v"],
    ),
    "qwen3.5": Expectation(
        omnidocbench="87.7 (v1.5, self-reported; превосходит qwen3-vl-30b: 86.8)",
        claimed_speed="н/д; ~40–80 tok/s на 16GB GPU (oamazonasgabriel/qwen3.5-9b)",
        disk="19.3 ГБ BF16 / ~6.6 ГБ Q4_K_M (9.65B параметров, dense)",
        russian="201 язык (текст); OCR-языки — н/д, но кириллица в составе мультиязычного OCR",
        sources=[
            "https://huggingface.co/Qwen/Qwen3.5-9B",
            "https://ollama.com/library/qwen3.5:9b",
            "https://github.com/QwenLM/Qwen3.5",
        ],
    ),
    "GLM-4.6V-Flash-9B": Expectation(
        omnidocbench="н/д (GLM-V серия не мерилась на OmniDocBench публично)",
        claimed_speed="н/д",
        disk="~6 ГБ Q4 (9B параметров, Flash-вариант — облегченный)",
        russian="zh/en заявлены; ru — н/д (GLM-4V-9B имел zh/en, GLM-4.6V расширил)",
        sources=[
            "https://github.com/zai-org/GLM-V",
            "https://ollama.com/haervwe/GLM-4.6V-Flash-9B",
            "https://huggingface.co/zai-org/GLM-4.1V-9B-Thinking",
        ],
    ),
}

_NO_EXPECTATION = Expectation(
    omnidocbench="н/д (модель не входила в ресёрч — дополнить docs/ocr-models-research)",
    claimed_speed="н/д", disk="н/д", russian="н/д", sources=[],
)


def expectation_for(model: str) -> Expectation:
    return EXPECTATIONS.get(model.split(":")[0], _NO_EXPECTATION)


def gpu_info() -> str:
    """Железо прогона — без него колонка «скорость» бессмысленна для сравнения."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            timeout=10, text=True,
        ).strip()
        return out or "GPU не обнаружен"
    except (OSError, subprocess.SubprocessError):
        return "nvidia-smi недоступен (CPU или не-NVIDIA)"


def ollama_models(url: str = "http://localhost:11434") -> set[str]:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=10) as resp:
            data = json.load(resp)
        return {m["name"] for m in data.get("models", [])}
    except (OSError, ValueError):
        return set()


def ensure_models(models: list[str], pull: bool) -> list[str]:
    """Проверяет наличие моделей в Ollama; с --pull подтягивает отсутствующие."""
    have = ollama_models()
    ready = []
    for m in models:
        name = m if ":" in m else f"{m}:latest"
        if m in have or name in have:
            ready.append(m)
            continue
        if pull:
            print(f"[PULL] ollama pull {m} …", flush=True)
            proc = subprocess.run(["ollama", "pull", m], timeout=3600)
            if proc.returncode == 0:
                ready.append(m)
            else:
                print(f"[PULL] {m}: не удалось подтянуть — пропускаю", flush=True)
        else:
            print(f"[SKIP] {m}: нет в Ollama (добавьте --pull или `ollama pull {m}`)",
                  flush=True)
    return ready


def verdict(r: ModelResult) -> str:
    """Короткий вердикт по порогам проекта (100% была базовая точность qwen3-vl)."""
    if r.error and not r.docs:
        return "⛔ прогон не удался"
    if r.accuracy >= 0.99 and r.pass_rate >= 0.99:
        return "✅ соответствует"
    if r.accuracy >= 0.90:
        return "⚠️ ниже базовой"
    return "❌ существенно ниже"


def render_console(results: list[ModelResult]) -> str:
    """Наглядная таблица в терминал."""
    lines = [
        f"{'Модель':<26} {'Точность':>9} {'PASS':>9} {'с/док':>7} {'Вердикт':<22}",
        "-" * 78,
    ]
    for r in results:
        lines.append(
            f"{r.model:<26} {r.accuracy:>8.1%} {r.passed:>4}/{r.num_docs:<4} "
            f"{r.avg_time_per_doc:>6.1f} {verdict(r):<22}"
        )
    return "\n".join(lines)


def render_report(results: list[ModelResult], hw: str) -> str:
    """Markdown-отчёт «ожидания vs реальность» — фактура для статьи на Хабре."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc_count = max((r.num_docs for r in results), default=0)
    out = [
        "# Ожидания vs реальность: локальные OCR/VLM на русских медицинских документах",
        "",
        f"*Прогон: {now}. Железо: {hw}.*",
        f"*Корпус: {doc_count} документов с эталонной разметкой "
        "(tests/fixtures/documents/samples/, sidecar `.expected.json`).*",
        "",
        "**Как читать.** «Ожидание» — публичные бенчи из model card/лидербордов "
        "(источники — docs/ocr-models-research-2026-07.md, Часть 2). «Реальность» — "
        "точность извлечения эталонных значений и время на документ ЭТОГО прогона. "
        "Прямое численное сравнение колонок некорректно (OmniDocBench не содержит "
        "русского, скорость вендоры меряют на другом железе) — в этом и смысл таблицы.",
        "",
        "| Модель | Ожидание: OmniDocBench | Ожидание: скорость | Реальность: точность | Реальность: PASS | Реальность: с/док | Вердикт |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        e = expectation_for(r.model)
        out.append(
            f"| **{r.model}** | {e.omnidocbench} | {e.claimed_speed} "
            f"| **{r.accuracy:.1%}** ({r.total_matched}/{r.total_expected}) "
            f"| {r.passed}/{r.num_docs} "
            f"| {r.avg_time_per_doc:.1f} | {verdict(r)} |"
        )
    out += ["", "## Паспорт ожиданий (первоисточники)", ""]
    for r in results:
        e = expectation_for(r.model)
        out += [
            f"### {r.model}",
            f"- Диск: {e.disk}",
            f"- Русский: {e.russian}",
            "- Источники: " + ("; ".join(e.sources) if e.sources else "—"),
            "",
        ]
    out += ["## Детали прогона по документам", ""]
    for r in results:
        out.append(f"### {r.model}")
        if r.error:
            out.append(f"- ошибка: {r.error}")
        fails = [d for d in r.docs if d.status == "FAIL"]
        if fails:
            out.append("- провалы: " + ", ".join(
                f"{d.name} ({d.matched}/{d.expected})" for d in fails))
        else:
            out.append("- провалов нет")
        out.append(f"- classify: {r.total_classify_s:.0f}s, extract: "
                   f"{r.total_extract_s:.0f}s, wall: {r.wall_s:.0f}s")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--pull", action="store_true",
                    help="подтянуть отсутствующие модели через ollama pull")
    ap.add_argument("--skip-synthetic", action="store_true",
                    help="только реальные документы (без синтетического бланка)")
    ap.add_argument("--timeout", type=int, default=7200, help="сек на модель")
    args = ap.parse_args()

    hw = gpu_info()
    print(f"[HW] {hw}")
    models = ensure_models(args.models, args.pull)
    if not models:
        print("Нет доступных моделей — нечего прогонять.", file=sys.stderr)
        return 1

    results: list[ModelResult] = []
    for model in models:
        results.append(run_model(model, skip_synthetic=args.skip_synthetic,
                                 timeout=args.timeout))

    # Сырые числа — для воспроизводимости и последующего анализа.
    RESULTS_FILE.write_text(json.dumps(
        [{k: v for k, v in asdict(r).items() if k != "raw_output"} for r in results],
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    report = render_report(results, hw)
    REPORT_FILE.write_text(report, encoding="utf-8")

    print("\n" + "=" * 78)
    print("ОЖИДАНИЯ vs РЕАЛЬНОСТЬ")
    print("=" * 78)
    print(render_console(results))
    print(f"\nОтчёт: {REPORT_FILE}")
    print(f"Сырые данные: {RESULTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
