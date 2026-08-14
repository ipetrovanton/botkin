from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from deep_model_benchmark import _chat, _stop_all_models, atomic_write_json, extract_evidence_ids
from hardware_telemetry import TelemetrySession

MODELS = ("huihui_ai/Qwen3.6-abliterated:35b-a3b", "gemma4:26b-a4b-it-qat")
GARMIN_KEY = "Петров Антон Игоревич|24.02.1993"


def _package_prompt(package: dict) -> tuple[str, str]:
    patient = package["patient"]
    system = (
        "Ты медицинский аналитик. Работай только с FACT_PACKAGE этого пациента. "
        "Не смешивай его с другими пациентами. Не выдумывай значения, диагнозы, препараты, "
        "даты анализов, даты заключений, погоду или данные Garmin. "
        "Если даты нет, напиши «дата отсутствует». Если external.weather.available=false, "
        "напиши, что погодных данных нет. Каждое patient-specific утверждение сопровождай "
        "реальным evidence ID из FACT_PACKAGE. Отделяй ФАКТ, ИНТЕРПРЕТАЦИЮ и ГИПОТЕЗУ. "
        "Отвечай по-русски."
    )
    user = f"""PATIENT_SCOPE: {patient['patient_key']}
GARMIN_ATTACHED: {patient['garmin_attached']}
MISSING_DATES: {json.dumps(patient['missing_dates'], ensure_ascii=False)}
FACT_PACKAGE:
{json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}

Составь подробный отчёт только по этому пациенту:
1. подтверждённые лабораторные отклонения и динамика;
2. заключения врачей с датами;
3. назначенные препараты и схемы только из источников;
4. Garmin и физическая активность — только если GARMIN_ATTACHED=true;
5. погода и внешние факторы — только при наличии фактов;
6. возможные связи и альтернативные гипотезы;
7. рекомендации, что обсудить с врачом;
8. отсутствующие данные и итог.
"""
    return system, user


def _score_output(text: str, package: dict) -> dict:
    valid = set()
    for domain in ("labs", "reports", "medications", "health", "activities", "sources"):
        valid.update(item["id"] for item in package["facts"].get(domain, []) if item.get("id"))
    cited = extract_evidence_ids(text)
    invalid = sorted(cited - valid)
    lower = text.lower()
    patient = package["patient"]
    garmin_mentioned = any(word in lower for word in ("garmin", "hrv", "body battery"))
    garmin_absent = bool(re.search(r"(нет|отсутств|не предостав|не подключ).{0,80}(garmin|hrv|body battery)|(garmin|hrv|body battery).{0,80}(нет|отсутств|не предостав)", lower))
    garmin_leak = not patient["garmin_attached"] and garmin_mentioned and not garmin_absent
    weather_mentioned = any(word in lower for word in ("погода", "температура воздуха", "осадки", "давление атмосфер"))
    weather_absent = bool(re.search(r"(нет|отсутств|не предостав).{0,80}(погод|осадк|атмосфер)|(погод|осадк|атмосфер).{0,80}(нет|отсутств|не предостав)", lower))
    weather_leak = not package["external"]["weather"]["available"] and weather_mentioned and not weather_absent
    return {
        "cited": len(cited),
        "valid_cited": len(cited - set(invalid)),
        "invalid_ids": invalid,
        "garmin_leak": garmin_leak,
        "weather_leak": weather_leak,
        "passed_guards": not invalid and not garmin_leak and not weather_leak,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", type=Path, default=Path("benchmarks/e2e_patient_packages"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/e2e_patient_reports"))
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--rescore-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packages = []
    for path in sorted(args.packages.glob("*.json")):
        if path.name == "manifest.json":
            continue
        packages.append(json.loads(path.read_text(encoding="utf-8")))
    if args.rescore_only:
        results = []
        for model in MODELS:
            model_dir = args.output / model.replace("/", "_").replace(":", "_")
            for package in packages:
                key = package["patient"]["patient_key"]
                safe = re.sub(r"[^\w.-]+", "_", key)
                path = model_dir / f"{safe}.md"
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                results.append({"model": model, "patient_key": key, "error": None, "score": _score_output(text, package)})
        atomic_write_json(args.output / "results.rescored.json", results)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    results = []
    for model in MODELS:
        _stop_all_models()
        for package in packages:
            key = package["patient"]["patient_key"]
            safe = re.sub(r"[^\w.-]+", "_", key)
            model_dir = args.output / model.replace("/", "_").replace(":", "_")
            model_dir.mkdir(parents=True, exist_ok=True)
            prefix = model_dir / safe
            system, user = _package_prompt(package)
            record = {"model": model, "patient_key": key, "error": None}
            try:
                with TelemetrySession(prefix, interval_s=1.0) as session:
                    session.wait_ready()
                    response, elapsed = _chat(
                        model,
                        system,
                        user,
                        {"think": "high", "temperature": 0.2, "num_ctx": 16384, "num_predict": 12288},
                        seed=42,
                        timeout_s=args.timeout,
                    )
                text = (response.get("message") or {}).get("content") or ""
                score = _score_output(text, package)
                record.update({"wall_s": elapsed, "output_chars": len(text), "score": score, "telemetry": session.summary()})
                prefix.with_suffix(".md").write_text(text, encoding="utf-8")
            except Exception as exc:
                record["error"] = str(exc)
            results.append(record)
            atomic_write_json(args.output / "results.json", results)
            print(json.dumps({"model": model, "patient_key": key, "error": record["error"], "score": record.get("score")}, ensure_ascii=False), flush=True)
        _stop_all_models()
    return 0 if all(not item["error"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
