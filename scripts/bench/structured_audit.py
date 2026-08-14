from __future__ import annotations

import argparse
import json
from pathlib import Path

from deep_model_benchmark import (
    _chat,
    _stop_all_models,
    atomic_write_json,
    batch_audit_json_schema,
    load_fact_package,
    score_fact_audit,
    validate_fact_audit,
)
from hardware_telemetry import TelemetrySession


MODELS = (
    "huihui_ai/Qwen3.6-abliterated:35b-a3b",
    "gemma4:31b-it-q4_K_M",
    "gemma4:26b-a4b-it-qat",
    "medgemma:27b-it-q4_K_M",
)
_AUDIT_ARRAY_FIELDS = ("lab_assertions", "date_assertions", "medication_assertions", "contradictions", "findings", "missing_data")


def _chunks(items: list[dict], size: int) -> list[list[dict]]:
    return [items[start:start + size] for start in range(0, len(items), size)]


def _findings_subset(package: dict, max_labs: int = 20, max_reports: int = 20, max_medications: int = 20) -> tuple[list[str], list[dict]]:
    facts = package["facts"]
    abnormal_labs = [item for item in facts.get("labs", []) if item.get("status") in ("low", "high")]
    lab_records = abnormal_labs[:max_labs] if len(abnormal_labs) > max_labs else abnormal_labs
    report_records = (facts.get("reports", []) or [])[:max_reports]
    medication_records = (facts.get("medications", []) or [])[:max_medications]
    records = lab_records + report_records + medication_records
    return [item["id"] for item in records if item.get("id")], records


def domain_batches(
    package: dict,
    lab_batch_size: int = 32,
    medication_batch_size: int = 20,
    include_findings: bool = False,
) -> list[dict]:
    facts = package["facts"]
    batches = []
    for domain, records, size in (
        ("labs", facts.get("labs", []), lab_batch_size),
        ("dates", facts.get("reports", []), len(facts.get("reports", [])) or 1),
        ("medications", facts.get("medications", []), medication_batch_size),
    ):
        for chunk in _chunks(records, size):
            batches.append({"domain": domain, "expected_ids": [item["id"] for item in chunk], "facts": chunk})
    record_by_id = {item["id"]: item for item in facts.get("labs", [])}
    for series in facts.get("lab_series", []):
        ids = series.get("fact_ids", [])[:2]
        if len(ids) == 2:
            batches.append({"domain": "contradictions", "expected_ids": ids, "facts": [record_by_id[item] for item in ids if item in record_by_id]})
    if include_findings:
        expected_ids, records = _findings_subset(package)
        if expected_ids:
            batches.append({"domain": "findings", "expected_ids": expected_ids, "facts": records})
    return batches


def merge_audits(audits: list[dict]) -> dict:
    merged = {field: [] for field in _AUDIT_ARRAY_FIELDS}
    for audit in audits:
        for field in _AUDIT_ARRAY_FIELDS:
            merged[field].extend(audit.get(field, []))
    return merged


def audit_config() -> dict:
    return {"think": False, "temperature": 0.0, "seed": 42, "num_ctx": 16384, "num_predict": 8192}


def model_audit_config(model: str, lab_batch_size: int | None = None) -> tuple[dict, int]:
    base = audit_config()
    if "gemma" in model.lower():
        config = {**base, "num_predict": 12288}
        size = lab_batch_size if lab_batch_size is not None else 16
    else:
        config = base
        size = lab_batch_size if lab_batch_size is not None else 32
    return config, size


def build_golden(package: dict, include_findings: bool = False) -> dict:
    facts = package["facts"]
    required_lab_ids = [
        item["id"] for item in facts["labs"] if item.get("status") in {"low", "high"}
    ]
    required_date_ids = [item["id"] for item in facts["reports"] if item.get("visit_date")]
    required_medication_ids = [item["id"] for item in facts["medications"]]
    contradiction_groups = []
    for series in facts["lab_series"]:
        if len(series.get("fact_ids", [])) >= 2:
            contradiction_groups.append(series["fact_ids"][:2])
    findings_ids, _ = _findings_subset(package) if include_findings else ([], [])
    return {
        "required_lab_ids": required_lab_ids,
        "required_date_ids": required_date_ids,
        "required_medication_ids": required_medication_ids,
        "required_contradiction_groups": contradiction_groups[:20],
        "required_finding_evidence": 1 if include_findings and findings_ids else 0,
    }


def build_domain_prompt(package: dict, batch: dict) -> tuple[str, str]:
    if batch["domain"] == "findings":
        system = (
            "Ты анализируешь ключевые факты пациента. "
            "Отвечай строго JSON по schema. Не добавляй Markdown и дополнительные ключи. "
            "temperature=0. Для patient-specific выводов используй только предоставленные IDs: "
            "LAB, REP, MED. Не используй SRC или внешние источники как доказательство."
        )
        user = (
            f"PATIENT_SCOPE: {package['patient']['patient_key']}\n"
            f"КЛЮЧЕВЫЕ ФАКТЫ IDS: {json.dumps(batch['expected_ids'], ensure_ascii=False)}\n"
            f"FACTS: {json.dumps(batch['facts'], ensure_ascii=False, sort_keys=True)}\n\n"
            "Заполни массив `findings`: 3-5 ключевых выводов. "
            "Каждый объект: type='FACT|ИНТЕРПРЕТАЦИЯ|ГИПОТЕЗА', text, confidence='high|medium|low', "
            "evidence_ids из КЛЮЧЕВЫЕ ФАКТЫ IDS. Все остальные массивы оставь пустыми."
        )
        return system, user
    fields = {
        "labs": "lab_assertions",
        "dates": "date_assertions",
        "medications": "medication_assertions",
        "contradictions": "contradictions",
    }
    field = fields[batch["domain"]]
    instructions = {
        "labs": (
            "Для КАЖДОГО ID из ОБЯЗАТЕЛЬНЫЕ IDS создай объект lab_assertions. "
            "evidence_ids ОБЯЗАН содержать ровно один ID этого факта. "
            "name, value_num (точное число из value_num; null только если в FACTS value_num=null), "
            "unit (точно из FACTS; null только если unit=null), status (из FACTS). "
            "Не опускай поля, не округляй числа."
        ),
        "dates": (
            "Для КАЖДОГО ID создай объект date_assertions с evidence_ids и date (visit_date из FACTS)."
        ),
        "medications": (
            "Для КАЖДОГО ID создай объект medication_assertions с evidence_ids, raw, canonical, schedule. "
            "Берёшь значения из FACTS; null если отсутствует."
        ),
        "contradictions": (
            "FACTS содержат РОВНО два лабораторных результата одного показателя. "
            "Для этой пары ОБЯЗАТЕЛЬНО создай один объект contradiction. "
            "Опиши, что изменилось: значение, статус, единица или наличие/отсутствие результата. "
            "Даже если второе значение null/unknown/текст, а первое числовое — это изменение, его нужно описать. "
            "evidence_ids обязан содержать ОБА ID из FACTS."
        ),
    }
    system = (
        "Ты проверяешь только один домен замороженного FACT_PACKAGE. "
        "Отвечай строго JSON по schema. Не добавляй Markdown и дополнительные ключи. "
        "temperature=0, не делай свободных медицинских рекомендаций."
    )
    user = (
        f"ДОМЕН: {batch['domain']}\n"
        f"ОБЯЗАТЕЛЬНЫЕ IDS: {json.dumps(batch['expected_ids'], ensure_ascii=False)}\n"
        f"FACTS: {json.dumps(batch['facts'], ensure_ascii=False, sort_keys=True)}\n\n"
        f"{instructions[batch['domain']]} "
        f"Заполни массив {field}. Все остальные массивы оставь пустыми."
    )
    return system, user


def run_one(model: str, package: dict, output_dir: Path, timeout_s: float, include_findings: bool = False, lab_batch_size: int | None = None) -> dict:
    _stop_all_models()
    model_dir = output_dir / model.replace("/", "_").replace(":", "_")
    model_dir.mkdir(parents=True, exist_ok=True)
    config, size = model_audit_config(model, lab_batch_size=lab_batch_size)
    batches = domain_batches(package, include_findings=include_findings, lab_batch_size=size)
    parsed_audits = []
    batch_results = []
    base_config = config
    for index, batch in enumerate(batches):
        system, user = build_domain_prompt(package, batch)
        prefix = model_dir / f"batch_{index:03d}_{batch['domain']}"
        batch_result = {"domain": batch["domain"], "expected_ids": batch["expected_ids"], "error": None}
        expected_count = len(batch["expected_ids"])
        if batch["domain"] == "contradictions":
            assertion_count = 1
        elif batch["domain"] == "findings":
            assertion_count = 0
        else:
            assertion_count = expected_count
        counts = {
            "labs": assertion_count if batch["domain"] == "labs" else 0,
            "dates": assertion_count if batch["domain"] == "dates" else 0,
            "medications": assertion_count if batch["domain"] == "medications" else 0,
            "contradictions": assertion_count if batch["domain"] == "contradictions" else 0,
            "findings": assertion_count if batch["domain"] == "findings" else 0,
        }
        schema = batch_audit_json_schema(counts, allowed_evidence_ids=batch["expected_ids"])
        if batch["domain"] == "findings":
            schema["properties"]["findings"]["maxItems"] = 5
            schema["$defs"]["FindingAssertion"]["properties"]["evidence_ids"]["minItems"] = 1
            schema["$defs"]["FindingAssertion"]["properties"]["evidence_ids"]["maxItems"] = len(batch["expected_ids"])
        if batch["domain"] == "contradictions":
            schema["$defs"]["ContradictionAssertion"]["properties"]["evidence_ids"]["minItems"] = 2
            schema["$defs"]["ContradictionAssertion"]["properties"]["evidence_ids"]["maxItems"] = 2
        config = {**base_config, "format_schema": schema}
        try:
            with TelemetrySession(prefix, interval_s=1.0) as session:
                session.wait_ready()
                response, elapsed = _chat(model, system, user, config, seed=42, timeout_s=timeout_s)
            content = (response.get("message") or {}).get("content") or ""
            prefix.with_suffix(".output.json").write_text(content, encoding="utf-8")
            parsed = validate_fact_audit(json.loads(content))
            parsed_audits.append(parsed.model_dump())
            batch_result.update({
                "wall_s": elapsed,
                "output_chars": len(content),
                "output_tokens": response.get("eval_count", 0),
                "telemetry": session.summary(),
            })
        except Exception as exc:
            batch_result["error"] = str(exc)
        batch_results.append(batch_result)
        atomic_write_json(model_dir / "batches.json", batch_results)
    merged = merge_audits(parsed_audits)
    merged_path = model_dir / "merged.audit.json"
    atomic_write_json(merged_path, merged)
    errors = [batch.get("error") for batch in batch_results if batch.get("error")]
    result = {
        "model": model,
        "batches": batch_results,
        "score": score_fact_audit(validate_fact_audit(merged), package, build_golden(package, include_findings=include_findings)),
        "error": errors[0] if errors else None,
    }
    atomic_write_json(model_dir / "structured_audit.result.json", result)
    _stop_all_models()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data") / "botkin.db")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("benchmarks") / "structured_audit_q8")
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    package = load_fact_package(args.db, args.user_id)
    atomic_write_json(args.output / "fact_package.manifest.json", {
        "sha256": package["sha256"],
        "golden": build_golden(package),
    })
    results = []
    for model in args.models:
        result = run_one(model, package, args.output, args.timeout)
        results.append(result)
        atomic_write_json(args.output / "results.json", results)
        print(json.dumps({"model": model, "error": result.get("error"), "score": result.get("score")}, ensure_ascii=False), flush=True)
    _stop_all_models()
    return 0 if all(not result.get("error") for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
