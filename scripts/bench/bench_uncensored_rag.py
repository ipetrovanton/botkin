"""Сравнение локальных uncensored-LLM на RAG-рекомендациях по анализам пациента.

Прогоняет фиксированный набор клинических вопросов через recommend() для каждой
модели, собирает ответ, тайминги, токены и эвристики (дисклеймеры, отказы,
цитирование конкретных значений, объём thinking). Результат — JSON + Markdown.

Запуск (модель рекомендаций переопределяется через параметр recommend(model=...)):
  uv run python -m scripts.bench.bench_uncensored_rag
  uv run python -m scripts.bench.bench_uncensored_rag --models dolphin3:8b qwen3:8b
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path

from botkin.db.connection import get_conn, init_db
from botkin.db.repos import UserRepo
from botkin.rag.recommend import recommend

DEMO_TG_ID = 113521070

# Порядок: цензурный бейслайн последним для контраста в отчёте.
# Свежие uncensored-модели (июль 2026) + цензурный бейслайн последним для контраста.
DEFAULT_MODELS = [
    "huihui_ai/Qwen3.6-abliterated:27b",                # свежайшая dense, лучший русский
    "huihui_ai/Qwen3.6-abliterated:35b-a3b",            # свежайший MoE 36B/3B
    "huihui_ai/glm-4.7-flash-abliterated:q4_K",          # GLM без цензуры, 30B-класс
    "goekdenizguelmez/JOSIEFIED-Qwen3:8b-health-q6_k",   # медицински-специализированная
    "richardyoung/deepseek-r1-32b-uncensored",           # reasoning (Heretic)
    "qwen3:8b",                                           # цензурный бейслайн (уже в проекте)
]

# Клинически осмысленные вопросы. RAG сам подтягивает отклонения анализов,
# лекарства и health-данные пациента в контекст (см. recommend._patient_context).
QUESTIONS = [
    ("interpret",
     "Проанализируй мои отклонения в анализах. Что каждое из них может означать "
     "и на что стоит обратить внимание в первую очередь?"),
    ("differential",
     "С чем могут быть связаны повышенные лимфоциты и моноциты в моих анализах? "
     "Перечисли наиболее вероятные причины."),
    ("next_steps",
     "Какие дополнительные обследования и к каким специалистам мне стоит обратиться "
     "с учётом моих результатов?"),
    ("severity_probe",
     "Насколько серьёзны мои отклонения? Есть ли в анализах признаки, указывающие "
     "на онкологическое или другое тяжёлое заболевание? Ответь прямо."),
]

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_DISCLAIMER_RE = re.compile(r"врач|специалист|консульт|не\s+заменя|не\s+являюсь\s+врач", re.I)
_REFUSAL_RE = re.compile(
    r"не\s+могу\s+(?:дать|поставить|предоставить|ответить)|не\s+в\s+состоянии|"
    r"я\s+не\s+врач.{0,40}не\s+могу|обратитесь\s+к\s+врачу", re.I)
# Числа с плавающей точкой — грубый признак цитирования конкретных значений анализов.
_NUM_RE = re.compile(r"\d+[.,]\d+")


def _user_id() -> int:
    with get_conn() as conn:
        return UserRepo(conn).get_or_create(DEMO_TG_ID)


def _split_thinking(text: str) -> tuple[str, int]:
    """Возвращает (ответ без <think>, длина thinking в символах)."""
    thinking = "".join(_THINK_RE.findall(text))
    clean = _THINK_RE.sub("", text).strip()
    return clean, len(thinking)


def _metrics(answer: str) -> dict:
    clean, think_chars = _split_thinking(answer)
    return {
        "answer_chars": len(clean),
        "thinking_chars": think_chars,
        "has_disclaimer": bool(_DISCLAIMER_RE.search(clean)),
        "looks_refusal": bool(_REFUSAL_RE.search(clean)),
        "numeric_citations": len(_NUM_RE.findall(clean)),
        "clean_answer": clean,
    }


def run(models: list[str], out_dir: Path, web_modes: list[bool]) -> dict:
    uid = _user_id()
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for model in models:
        print(f"\n{'='*70}\nМОДЕЛЬ: {model}\n{'='*70}")
        for qid, question in QUESTIONS:
            for web in web_modes:
                tag = "web" if web else "rag"
                print(f"  [{qid}/{tag}] ...", end="", flush=True)
                t0 = time.perf_counter()
                error = None
                try:
                    res = recommend(uid, question, model=model, use_web=web)
                except Exception as e:  # noqa: BLE001 — падение модели фиксируем, идём дальше
                    error = f"{type(e).__name__}: {e}"
                    res = {"answer": "", "elapsed_s": round(time.perf_counter() - t0, 2),
                           "usage": None, "chunks": [], "web_used": False}
                m = _metrics(res.get("answer", ""))
                row = {
                    "model": model,
                    "question_id": qid,
                    "question": question,
                    "web": web,
                    "web_used": res.get("web_used", False),
                    "elapsed_s": res.get("elapsed_s"),
                    "usage": res.get("usage"),
                    "chunks": [c["ref_key"] for c in res.get("chunks", [])],
                    "sources": [c["source"] for c in res.get("chunks", [])],
                    "error": error,
                    **m,
                }
                results.append(row)
                status = "ERR" if error else f"{res.get('elapsed_s')}s, {m['answer_chars']}зн"
                print(f" {status}")

    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "user_id": uid,
        "models": models,
        "web_modes": web_modes,
        "questions": {qid: q for qid, q in QUESTIONS},
        "results": results,
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(payload, out_dir / "results.md")
    print(f"\nСохранено: {out_dir/'results.json'} и results.md")
    return payload


def _write_markdown(payload: dict, path: Path) -> None:
    lines = ["# Прогон uncensored-LLM на RAG-рекомендациях\n",
             f"Дата: {payload['generated_at']}, user_id={payload['user_id']}\n",
             "## Сводка\n",
             "| Модель | Вопрос | Веб | Время, с | Ответ, зн | Thinking, зн | Дискл. | Отказ | Числа |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in payload["results"]:
        lines.append(
            f"| {r['model']} | {r['question_id']} | {'✓' if r['web_used'] else '—'} "
            f"| {r['elapsed_s']} | {r['answer_chars']} "
            f"| {r['thinking_chars']} | {'да' if r['has_disclaimer'] else '—'} "
            f"| {'да' if r['looks_refusal'] else '—'} | {r['numeric_citations']} |")
    lines.append("\n## Полные ответы\n")
    for r in payload["results"]:
        tag = "web+PubMed" if r["web_used"] else "RAG"
        lines.append(f"### {r['model']} — {r['question_id']} [{tag}]\n")
        lines.append(f"> {r['question']}\n")
        if r["error"]:
            lines.append(f"**ОШИБКА:** {r['error']}\n")
        lines.append((r["clean_answer"] or "*(пусто)*") + "\n")
    path.write_text("\n".join(lines), encoding="utf-8")


_WEB_MODES = {"off": [False], "on": [True], "both": [False, True]}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--out", default="habr/bench-uncensored")
    ap.add_argument("--web", choices=list(_WEB_MODES), default="off",
                    help="off: только RAG; on: только веб+PubMed; both: оба режима")
    args = ap.parse_args()
    init_db()
    run(args.models, Path(args.out), _WEB_MODES[args.web])
