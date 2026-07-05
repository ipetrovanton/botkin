"""Анализ результатов прогона uncensored-LLM: агрегаты + графики (PNG) для статьи.

Читает results.json (от bench_uncensored_rag) и строит:
- среднее время ответа по моделям;
- средняя длина ответа и объём thinking;
- доля ответов с дисклеймером / с отказом;
- среднее число цитируемых значений (numeric_citations);
- использование research-источника (доля research-чанков в контексте);
- эффект веб-доступа (если прогон был с --web both).

Запуск:
  uv run python -m scripts.bench.analyze_uncensored_rag --in habr/bench-uncensored
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

import plotly.graph_objects as go


def _short(model: str) -> str:
    """Компактное имя модели для осей графика."""
    return model.split("/")[-1].replace("-abliterated", "-abl").replace(":latest", "")


def _agg(results: list[dict], key) -> dict[str, list]:
    by_model: dict[str, list] = defaultdict(list)
    for r in results:
        v = key(r)
        if v is not None:
            by_model[r["model"]].append(v)
    return by_model


def _bar(models: list[str], values: list[float], title: str, ytitle: str, path: Path,
         color: str = "#2c7fb8") -> None:
    fig = go.Figure(go.Bar(x=[_short(m) for m in models], y=values,
                           marker_color=color, text=[f"{v:.1f}" for v in values],
                           textposition="outside"))
    fig.update_layout(title=title, yaxis_title=ytitle, template="plotly_white",
                      height=500, width=900, margin=dict(t=60, b=120))
    fig.update_xaxes(tickangle=-30)
    fig.write_image(str(path))
    print("график:", path)


def analyze(in_dir: Path) -> None:
    payload = json.loads((in_dir / "results.json").read_text(encoding="utf-8"))
    results = payload["results"]
    models = payload["models"]

    # только RAG-режим для честного сравнения моделей (без веб-шума)
    rag = [r for r in results if not r["web_used"]]
    if not rag:
        rag = results

    def avg(key):
        d = _agg(rag, key)
        return [st.mean(d[m]) if d.get(m) else 0 for m in models]

    _bar(models, avg(lambda r: r["elapsed_s"]),
         "Среднее время ответа (RAG-режим)", "секунды",
         in_dir / "chart_latency.png", "#d95f0e")
    _bar(models, avg(lambda r: r["answer_chars"]),
         "Средняя длина ответа", "символы", in_dir / "chart_length.png", "#2c7fb8")
    _bar(models, avg(lambda r: r["thinking_chars"]),
         "Средний объём thinking (<think>)", "символы",
         in_dir / "chart_thinking.png", "#756bb1")
    _bar(models, avg(lambda r: r["numeric_citations"]),
         "Среднее число цитируемых значений", "шт.",
         in_dir / "chart_numbers.png", "#31a354")

    # доля research-чанков в контексте
    def research_share(r):
        srcs = r.get("sources") or []
        return 100 * sum(s == "research" for s in srcs) / len(srcs) if srcs else 0
    _bar(models, avg(research_share),
         "Доля research-источников (PubMed) в контексте", "%",
         in_dir / "chart_research_share.png", "#c51b8a")

    # текстовая сводка дисклеймеров/отказов
    lines = ["# Аналитика прогона\n", f"Дата: {payload['generated_at']}\n",
             "| Модель | ответов | ср. время, с | ср. длина | дискл., % | отказ, % | ср. числа |",
             "|---|---|---|---|---|---|---|"]
    for m in models:
        rows = [r for r in rag if r["model"] == m]
        if not rows:
            continue
        n = len(rows)
        lines.append(
            f"| {_short(m)} | {n} | {st.mean([r['elapsed_s'] for r in rows]):.1f} "
            f"| {st.mean([r['answer_chars'] for r in rows]):.0f} "
            f"| {100*sum(r['has_disclaimer'] for r in rows)/n:.0f} "
            f"| {100*sum(r['looks_refusal'] for r in rows)/n:.0f} "
            f"| {st.mean([r['numeric_citations'] for r in rows]):.1f} |")

    # эффект веба, если прогон был с обоими режимами
    if any(r["web"] for r in results) and any(not r["web"] for r in results):
        lines.append("\n## Эффект веб-доступа (длина ответа, ср. по моделям)\n")
        lines.append("| Модель | RAG | веб+PubMed |")
        lines.append("|---|---|---|")
        for m in models:
            off = [r["answer_chars"] for r in results if r["model"] == m and not r["web"]]
            on = [r["answer_chars"] for r in results if r["model"] == m and r["web"]]
            if off and on:
                lines.append(f"| {_short(m)} | {st.mean(off):.0f} | {st.mean(on):.0f} |")

    (in_dir / "analysis.md").write_text("\n".join(lines), encoding="utf-8")
    print("сводка:", in_dir / "analysis.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", default="habr/bench-uncensored")
    args = ap.parse_args()
    analyze(Path(args.in_dir))
