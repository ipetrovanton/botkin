"""Анализ результатов прогона uncensored-LLM: агрегаты + графики (PNG) для статьи.

Читает results.json (от bench_uncensored_rag) и строит:
- среднее время ответа по моделям;
- средняя длина ответа и объём thinking;
- доля ответов с дисклеймером / с отказом;
- среднее число цитируемых значений (numeric_citations);
- использование research-источника (доля research-чанков в контексте);
- эффект веб-доступа (если прогон был с --web both).

Запуск:
  uv run python -m research.bench.analyze_uncensored_rag --in habr/bench-uncensored
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# PIL-рендер вместо plotly/kaleido: kaleido 0.2.1 не стартует Chromium на Windows-хосте
# ("Failed to decode Chromium's standard error stream"), а графики здесь — простые столбики.
_W, _H = 1000, 560
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 70, 30, 70, 170


def _font(size: int):
    for p in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


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
         color: tuple = (44, 127, 184)) -> None:
    """Простой столбчатый график через PIL. Значения подписаны над столбцами."""
    img = Image.new("RGB", (_W, _H), "white")
    d = ImageDraw.Draw(img)
    f_title, f_lbl, f_val = _font(24), _font(15), _font(14)
    d.text((_PAD_L, 22), title, fill="black", font=f_title)
    d.text((8, _PAD_T - 20), ytitle, fill="#555", font=f_val)

    plot_h = _H - _PAD_T - _PAD_B
    plot_w = _W - _PAD_L - _PAD_R
    d.line([(_PAD_L, _PAD_T), (_PAD_L, _PAD_T + plot_h)], fill="#999", width=1)
    d.line([(_PAD_L, _PAD_T + plot_h), (_PAD_L + plot_w, _PAD_T + plot_h)], fill="#999", width=1)

    vmax = max(values) if values and max(values) > 0 else 1
    n = len(models)
    slot = plot_w / max(n, 1)
    bw = slot * 0.6
    for i, (m, v) in enumerate(zip(models, values)):
        x0 = _PAD_L + i * slot + (slot - bw) / 2
        bh = (v / vmax) * plot_h
        y0 = _PAD_T + plot_h - bh
        d.rectangle([x0, y0, x0 + bw, _PAD_T + plot_h], fill=color)
        val_txt = f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        d.text((x0 + bw / 2, y0 - 18), val_txt, fill="black", font=f_val, anchor="mb")
        # подпись модели — построчно вертикально под осью
        label = _short(m)
        d.text((x0 + bw / 2, _PAD_T + plot_h + 8), label[:22], fill="#222", font=f_lbl,
               anchor="ma")
        if len(label) > 22:
            d.text((x0 + bw / 2, _PAD_T + plot_h + 26), label[22:44], fill="#222", font=f_lbl,
                   anchor="ma")
    img.save(str(path))
    print("график:", path)


def _grouped_bar(models: list[str], series: list[tuple[str, list[float], tuple]],
                 title: str, ytitle: str, path: Path) -> None:
    """Сгруппированный столбчатый график: несколько серий на модель (RAG vs веб)."""
    img = Image.new("RGB", (_W, _H), "white")
    d = ImageDraw.Draw(img)
    f_title, f_lbl, f_val = _font(24), _font(15), _font(13)
    d.text((_PAD_L, 22), title, fill="black", font=f_title)
    d.text((8, _PAD_T - 20), ytitle, fill="#555", font=f_val)
    plot_h, plot_w = _H - _PAD_T - _PAD_B, _W - _PAD_L - _PAD_R
    d.line([(_PAD_L, _PAD_T + plot_h), (_PAD_L + plot_w, _PAD_T + plot_h)], fill="#999", width=1)
    vmax = max((max(vals) for _, vals, _ in series if vals), default=1) or 1
    n, k = len(models), len(series)
    slot = plot_w / max(n, 1)
    bw = slot * 0.7 / k
    for i, m in enumerate(models):
        for j, (_, vals, color) in enumerate(series):
            v = vals[i]
            x0 = _PAD_L + i * slot + (slot * 0.15) + j * bw
            bh = (v / vmax) * plot_h
            y0 = _PAD_T + plot_h - bh
            d.rectangle([x0, y0, x0 + bw, _PAD_T + plot_h], fill=color)
            d.text((x0 + bw / 2, y0 - 15), f"{v:.0f}", fill="black", font=f_val, anchor="mb")
        d.text((_PAD_L + i * slot + slot / 2, _PAD_T + plot_h + 8), _short(m)[:20],
               fill="#222", font=f_lbl, anchor="ma")
    # легенда
    for j, (name, _, color) in enumerate(series):
        lx = _PAD_L + j * 200
        d.rectangle([lx, _H - 30, lx + 16, _H - 16], fill=color)
        d.text((lx + 22, _H - 30), name, fill="#222", font=f_lbl)
    img.save(str(path))
    print("график:", path)


def analyze(in_dir: Path, web_dir: Path | None = None) -> None:
    payload = json.loads((in_dir / "results.json").read_text(encoding="utf-8"))
    results = payload["results"]
    models = payload["models"]
    web_results = []
    if web_dir and (web_dir / "results.json").exists():
        web_results = json.loads((web_dir / "results.json").read_text(encoding="utf-8"))["results"]
        results = results + web_results

    # только RAG-режим для честного сравнения моделей (без веб-шума)
    rag = [r for r in results if not r["web_used"]]
    if not rag:
        rag = results

    def avg(key):
        d = _agg(rag, key)
        return [st.mean(d[m]) if d.get(m) else 0 for m in models]

    _bar(models, avg(lambda r: r["elapsed_s"]),
         "Среднее время ответа (RAG-режим)", "секунды",
         in_dir / "chart_latency.png", (217, 95, 14))
    _bar(models, avg(lambda r: r["answer_chars"]),
         "Средняя длина ответа", "символы", in_dir / "chart_length.png", (44, 127, 184))
    _bar(models, avg(lambda r: r["thinking_chars"]),
         "Средний объём рассуждений (thinking)", "символы",
         in_dir / "chart_thinking.png", (117, 107, 177))
    _bar(models, avg(lambda r: r["numeric_citations"]),
         "Среднее число цитируемых значений", "шт.",
         in_dir / "chart_numbers.png", (49, 163, 84))

    # доля research-чанков в контексте
    def research_share(r):
        srcs = r.get("sources") or []
        return 100 * sum(s == "research" for s in srcs) / len(srcs) if srcs else 0
    _bar(models, avg(research_share),
         "Доля research-источников (PubMed) в контексте", "%",
         in_dir / "chart_research_share.png", (197, 27, 138))

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

    # эффект веб-доступа: сравнение того же вопроса RAG vs веб+PubMed
    if web_results:
        qids = {r["question_id"] for r in web_results}
        lines.append("\n## Эффект веб-доступа (вопрос(ы): " + ", ".join(sorted(qids)) + ")\n")
        lines.append("| Модель | RAG время,с | web время,с | RAG длина | web длина | web_used |")
        lines.append("|---|---|---|---|---|---|")
        rag_len, web_len = [], []
        for m in models:
            off = [r for r in results if r["model"] == m and not r["web"]
                   and r["question_id"] in qids]
            on = [r for r in results if r["model"] == m and r["web"]]
            if off and on:
                rl, wl = st.mean([r["answer_chars"] for r in off]), \
                    st.mean([r["answer_chars"] for r in on])
                rag_len.append(rl)
                web_len.append(wl)
                lines.append(
                    f"| {_short(m)} | {st.mean([r['elapsed_s'] for r in off]):.0f} "
                    f"| {st.mean([r['elapsed_s'] for r in on]):.0f} | {rl:.0f} | {wl:.0f} "
                    f"| {'да' if all(r['web_used'] for r in on) else 'частично'} |")
        if rag_len:
            _grouped_bar(models, [("RAG", rag_len, (44, 127, 184)),
                                  ("веб+PubMed", web_len, (197, 27, 138))],
                         "Длина ответа: RAG vs веб+PubMed (вопрос differential)", "символы",
                         in_dir / "chart_web_effect.png")

    (in_dir / "analysis.md").write_text("\n".join(lines), encoding="utf-8")
    print("сводка:", in_dir / "analysis.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", default="habr/bench-uncensored")
    ap.add_argument("--web-dir", dest="web_dir", default=None,
                    help="каталог с прогоном --web on для сравнения RAG vs веб")
    args = ap.parse_args()
    analyze(Path(args.in_dir), Path(args.web_dir) if args.web_dir else None)
