from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from deep_model_benchmark import _chat, _stop_all_models, atomic_write_json
from hardware_telemetry import TelemetrySession
from score_e2e_report import score_e2e_report


MODELS = (
    "huihui_ai/Qwen3.6-abliterated:35b-a3b",
    "gemma4:26b-a4b-it-qat",
)


_REPORT_SYSTEM = """Ты переписываешь тело уже верифицированного patient report связным медицинским языком (humanize).

В отчёте уже есть valid evidence IDs вида `LAB:...`, `REP:...`, `MED:...`, `HLT:...`, `ACT:...`.

Исходный отчёт уже содержит только подтверждённые факты с evidence IDs. Твоя задача — сохранить всё содержание и сделать текст более человечным, без потери точности.

Жёсткие правила:
- Каждое утверждение о конкретном пациенте сопровождай evidence ID в квадратных скобках: [LAB:...], [REP:...], [MED:...], [HLT:...], [ACT:...].
- Маркируй тип вывода: **ФАКТ** — прямые данные; *ИНТЕРПРЕТАЦИЯ* — связь фактов; _ГИПОТЕЗА_ — предположение, требующее проверки.
- Не добавляй новые даты, значения, препараты, диагнозы, погоду, Garmin-значения.
- Не удаляй существующие evidence IDs и не заменяй их на SRC.
- Не выдумывай факты, которых нет в исходном report.
- Рекомендации — только «что обсудить с врачом», не давай инструкций.
- Отвечай по-русски."""


def _strip_report_header(report: str) -> str:
    parts = report.split("\n## ", 1)
    if len(parts) == 2:
        return "## " + parts[1]
    return report


def _build_user_prompt(package: dict, report: str, summary: dict, garmin_summary: dict | None) -> str:
    report_body = _strip_report_header(report)
    patient_key = package["patient"]["patient_key"]
    lines = [
        f"Пациент (не evidence): {patient_key}",
        f"Garmin/weather (не evidence): {package['patient']['garmin_attached']}/{package['external']['weather']['available']}",
        "\nСЛУЖЕБНЫЕ СЛОВА/МЕТКИ НЕ ЯВЛЯЮТСЯ EVIDENCE: `patient_scope`, `verified_patient_audit`, `verified_patient_summary`, `Validation`, `GARMIN_ATTACHED`, `WEATHER_AVAILABLE`. Не цитируй их.",
    ]
    if garmin_summary:
        lines.extend(["\nVERIFIED_GARMIN_SUMMARY:", json.dumps(garmin_summary, ensure_ascii=False, indent=2, sort_keys=True)])
    else:
        lines.append("\nVERIFIED_GARMIN_SUMMARY: отсутствует")
    lines.extend(["\nVERIFIED_PATIENT_REPORT (источник фактов):", report_body])
    lines.append(f"""
Перепиши исходный VERIFIED_PATIENT_REPORT связным медицинским языком. Сохрани все основные секции и все evidence IDs.
Не добавляй факты, даты, значения, диагнозы, препараты, погоду, Garmin-метрики, которых нет в исходном report.
Не удаляй evidence IDs. Маркируй: **ФАКТ**, *ИНТЕРПРЕТАЦИЯ*, _ГИПОТЕЗА_.
Если раздел содержит таблицу — переведи её в связный текст с цитатами.

Исходная версия содержит {len(summary.get('findings', []))} выводов аудита. Сохрани их тип и confidence.
""")
    return "\n".join(lines)


def _safe_key(key: str) -> str:
    return re.sub(r"[^\w.-]+", "_", key)


def _load_patient_packages(packages_dir: Path, patient_key: str | None = None) -> list[dict]:
    packages = []
    for path in sorted(packages_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        package = json.loads(path.read_text(encoding="utf-8"))
        if patient_key is not None and package["patient"]["patient_key"] != patient_key:
            continue
        packages.append(package)
    return packages


def _load_summary(audit_dir: Path, patient_key: str, model: str) -> dict | None:
    patient_dir = audit_dir / _safe_key(patient_key)
    model_dir = patient_dir / _safe_key(model.replace("/", "_").replace(":", "_"))
    summary_path = model_dir / "verified_patient_summary.json"
    if not summary_path.exists():
        return None
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _load_report(audit_dir: Path, patient_key: str, model: str) -> str | None:
    patient_dir = audit_dir / _safe_key(patient_key)
    model_dir = patient_dir / _safe_key(model.replace("/", "_").replace(":", "_"))
    report_path = model_dir / "verified_patient_report.md"
    if not report_path.exists():
        return None
    return report_path.read_text(encoding="utf-8")


def _load_garmin_summary(garmin_dir: Path, model: str) -> dict | None:
    model_dir = garmin_dir / _safe_key(model.replace("/", "_").replace(":", "_"))
    summary_path = model_dir / "verified_garmin_summary.json"
    if not summary_path.exists():
        return None
    return json.loads(summary_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", type=Path, default=Path("benchmarks/e2e_patient_packages"))
    parser.add_argument("--audit-dir", type=Path, default=Path("benchmarks/e2e_patient_audit"))
    parser.add_argument("--garmin-dir", type=Path, default=Path("benchmarks/garmin_audit_strict3"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/e2e_patient_reports_verified"))
    parser.add_argument("--patient-key", type=str, default=None)
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    packages = _load_patient_packages(args.packages, patient_key=args.patient_key)
    all_results = []
    for package in packages:
        patient_key = package["patient"]["patient_key"]
        patient_dir = args.output / _safe_key(patient_key)
        patient_dir.mkdir(parents=True, exist_ok=True)
        for model in args.models:
            summary = _load_summary(args.audit_dir, patient_key, model)
            if summary is None:
                print(json.dumps({"model": model, "patient_key": patient_key, "skipped": True, "reason": "verified_patient_summary not found"}, ensure_ascii=False))
                continue
            report = _load_report(args.audit_dir, patient_key, model)
            if report is None:
                print(json.dumps({"model": model, "patient_key": patient_key, "skipped": True, "reason": "verified_patient_report not found"}, ensure_ascii=False))
                continue
            garmin_summary = _load_garmin_summary(args.garmin_dir, model) if package["patient"]["garmin_attached"] else None
            _stop_all_models()
            system = _REPORT_SYSTEM
            user = _build_user_prompt(package, report, summary, garmin_summary)
            model_dir = patient_dir / _safe_key(model.replace("/", "_").replace(":", "_"))
            model_dir.mkdir(parents=True, exist_ok=True)
            prefix = model_dir / "report"
            record = {"model": model, "patient_key": patient_key, "error": None}
            try:
                with TelemetrySession(prefix, interval_s=1.0) as session:
                    session.wait_ready()
                    response, elapsed = _chat(
                        model,
                        system,
                        user,
                        {"think": False, "temperature": 0.0, "num_ctx": 16384, "num_predict": 12288},
                        seed=42,
                        timeout_s=args.timeout,
                    )
                text = (response.get("message") or {}).get("content") or ""
                score = score_e2e_report(text, package, summary)
                record.update({
                    "wall_s": elapsed,
                    "output_chars": len(text),
                    "score": score,
                    "telemetry": session.summary(),
                })
                (model_dir / "report.md").write_text(text, encoding="utf-8")
                atomic_write_json(model_dir / "report.score.json", score)
            except Exception as exc:
                record["error"] = str(exc)
            all_results.append(record)
            print(json.dumps({"model": model, "patient_key": patient_key, "error": record["error"], "score": record.get("score")}, ensure_ascii=False), flush=True)
            _stop_all_models()
        atomic_write_json(args.output / "results.json", all_results)
    return 0 if all(not item["error"] for item in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
