from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fact_package import build_fact_package
from hardware_telemetry import TelemetrySession


_EVIDENCE_ID_RE = re.compile(r"\[([A-Z]+:[^\]\s]+)\]")


def _json_list(value: object) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def load_fact_package(db_path: Path | str, user_id: int) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        labs = conn.execute(
            """
            SELECT id, document_id, taken_at, COALESCE(analyte_canonical, analyte_name) AS name,
                   analyte_name AS raw_name, value_num, value_text, unit, unit_raw,
                   ref_low, ref_high, ref_text
            FROM lab_results WHERE user_id = ? ORDER BY taken_at, id
            """,
            (user_id,),
        ).fetchall()
        report_rows = conn.execute(
            """
            SELECT id, visit_date, diagnosis, doctor_name, department,
                   medications_json, recommendations_json
            FROM doctor_reports WHERE user_id = ? ORDER BY visit_date, id
            """,
            (user_id,),
        ).fetchall()
        reports = []
        for row in report_rows:
            item = dict(row)
            item["medications"] = [
                medication if isinstance(medication, dict) else {"raw": medication}
                for medication in _json_list(item.pop("medications_json"))
            ]
            item["recommendations"] = _json_list(item.pop("recommendations_json"))
            reports.append(item)
        health = conn.execute(
            """
            SELECT metric || ':' || date(taken_at) AS id, metric, date(taken_at) AS date,
                   ROUND(AVG(value_num), 4) AS average, ROUND(MIN(value_num), 4) AS minimum,
                   ROUND(MAX(value_num), 4) AS maximum, COUNT(*) AS observations, unit
            FROM health_metrics WHERE user_id = ? AND value_num IS NOT NULL
            GROUP BY metric, date(taken_at), unit ORDER BY metric, date(taken_at), unit
            """,
            (user_id,),
        ).fetchall()
        activities = conn.execute(
            """
            SELECT id, provider, external_id, activity_type, started_at, duration_s,
                   distance_m, calories, avg_hr, max_hr
            FROM health_activities WHERE user_id = ? ORDER BY started_at, id
            """,
            (user_id,),
        ).fetchall()
        sources = conn.execute(
            """
            SELECT id, source, ref_key, text, meta_json
            FROM rag_chunks WHERE source IN ('research', 'drugs', 'analytes')
            ORDER BY source, ref_key LIMIT 20
            """
        ).fetchall()
    return build_fact_package(
        labs=labs,
        reports=reports,
        health=health,
        activities=activities,
        sources=sources,
    )


_AUDIT_SYSTEM = """Ты анализируешь замороженный пакет медицинских фактов.
Любое утверждение о конкретном пациенте сопровождай evidence_ids.
Не изменяй значения молча, не выдумывай анализы, даты, препараты, PMID или URL.
Не давай свободных рекомендаций. Верни только JSON заданной схемы."""


_SYNTHESIS_SYSTEM = """Ты анализируешь замороженный пакет медицинских фактов.
Каждое существенное утверждение о пациенте сопровождай evidence_ids.
Явно маркируй каждый вывод как ФАКТ, ИНТЕРПРЕТАЦИЯ или ГИПОТЕЗА.
Не выдумывай анализы, даты, препараты, PMID или URL. Если данных недостаточно, так и напиши.
Отвечай по-русски."""


DEFAULT_MODELS = (
    "huihui_ai/Qwen3.6-abliterated:35b-a3b",
    "gemma4:31b-it-q4_K_M",
    "gemma4:26b-a4b-it-qat",
    "medgemma:27b-it-q4_K_M",
)

MODEL_CONFIGS = {
    "huihui_ai/Qwen3.6-abliterated:35b-a3b": {"think": "high"},
    "gemma4:31b-it-q4_K_M": {"think": "high"},
    "gemma4:26b-a4b-it-qat": {"think": "high"},
    "medgemma:27b-it-q4_K_M": {"think": False},
}


@dataclass
class RunResult:
    model: str
    scenario: str
    seed: int
    wall_s: float
    prompt_tokens: int
    output_tokens: int
    thinking_chars: int
    output_chars: int
    evidence: dict
    telemetry: dict
    thermally_constrained: bool = False
    error: str | None = None


class AuditBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ids: list[str] = Field(min_length=1)


class LabAssertion(AuditBase):
    name: str
    value_num: float | None = Field(...)
    unit: str | None = Field(...)
    status: Literal["low", "high", "normal", "unknown"]


class DateAssertion(AuditBase):
    date: str


class MedicationAssertion(AuditBase):
    raw: str | None = None
    canonical: str | None = None
    schedule: str | None = None


class ContradictionAssertion(AuditBase):
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=2)


class FindingAssertion(AuditBase):
    type: Literal["FACT", "ИНТЕРПРЕТАЦИЯ", "ГИПОТЕЗА"]
    text: str
    confidence: Literal["high", "medium", "low"]


class FactAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lab_assertions: list[LabAssertion] = Field(default_factory=list)
    date_assertions: list[DateAssertion] = Field(default_factory=list)
    medication_assertions: list[MedicationAssertion] = Field(default_factory=list)
    contradictions: list[ContradictionAssertion] = Field(default_factory=list)
    findings: list[FindingAssertion] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


def validate_fact_audit(payload: dict) -> FactAudit:
    return FactAudit.model_validate(payload)


def audit_json_schema() -> dict:
    return FactAudit.model_json_schema()


def batch_audit_json_schema(counts: dict[str, int], allowed_evidence_ids: list[str] | None = None) -> dict:
    """Return a schema that constrains array lengths for a single batch.

    `counts` maps domain name (labs/dates/medications/contradictions/findings)
    to the exact number of items expected in the corresponding array.
    Arrays with count 0 get maxItems=0; others get both minItems and maxItems.
    If `allowed_evidence_ids` is provided, every evidence_ids list must contain
    only IDs from this list, preventing hallucinated or out-of-batch IDs.
    """
    schema = FactAudit.model_json_schema()
    field_map = {
        "lab_assertions": "labs",
        "date_assertions": "dates",
        "medication_assertions": "medications",
        "contradictions": "contradictions",
        "findings": "findings",
    }
    for field, domain in field_map.items():
        props = schema.setdefault("properties", {})[field]
        count = counts.get(domain, 0)
        if count > 0:
            props["minItems"] = count
            props["maxItems"] = count
        else:
            props["maxItems"] = 0
    if allowed_evidence_ids:
        defs = schema.setdefault("$defs", {})
        for def_name in ("LabAssertion", "DateAssertion", "MedicationAssertion", "ContradictionAssertion", "FindingAssertion"):
            evidence = defs.get(def_name, {}).get("properties", {}).get("evidence_ids")
            if evidence:
                evidence["items"] = {"type": "string", "enum": sorted(allowed_evidence_ids)}
    return schema


def _package_json(package: dict) -> str:
    return json.dumps(package["facts"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_write_json(path: Path, data: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def all_fact_ids(package: dict) -> set[str]:
    facts = package["facts"]
    ids = set()
    for domain in ("labs", "reports", "medications", "health", "activities", "sources"):
        ids.update(str(item["id"]) for item in facts.get(domain, []) if item.get("id"))
    return ids


def _fact_records(package: dict) -> dict[str, dict]:
    records = {}
    for domain in ("labs", "reports", "medications", "health", "activities", "sources"):
        records.update({str(item["id"]): item for item in package["facts"].get(domain, []) if item.get("id")})
    return records


def _norm(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def _parse_date(value: object) -> str | None:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _assertion_ids(audit: FactAudit) -> set[str]:
    ids = set()
    for item in (
        *audit.lab_assertions,
        *audit.date_assertions,
        *audit.medication_assertions,
        *audit.contradictions,
        *audit.findings,
    ):
        ids.update(item.evidence_ids)
    return ids


def score_fact_audit(audit: FactAudit, package: dict, golden: dict) -> dict:
    records = _fact_records(package)
    invalid_ids = sorted(_assertion_ids(audit) - records.keys())
    lab_missing, lab_value_mismatches = [], []
    for expected_id in golden.get("required_lab_ids", []):
        assertion = next((item for item in audit.lab_assertions if expected_id in item.evidence_ids), None)
        fact = records.get(expected_id)
        if assertion is None or fact is None:
            lab_missing.append(expected_id)
            continue
        if (
            assertion.value_num != fact.get("value_num")
            or _norm(assertion.unit) != _norm(fact.get("unit"))
            or assertion.status != fact.get("status")
            or _norm(assertion.name) != _norm(fact.get("name"))
        ):
            lab_value_mismatches.append(expected_id)
    date_missing, date_mismatches = [], []
    for expected_id in golden.get("required_date_ids", []):
        assertion = next((item for item in audit.date_assertions if expected_id in item.evidence_ids), None)
        fact = records.get(expected_id)
        expected_date = (fact or {}).get("visit_date") or (fact or {}).get("taken_at")
        parsed_assertion = _parse_date(assertion.date)
        parsed_expected = _parse_date(expected_date)
        if assertion is None or fact is None:
            date_missing.append(expected_id)
        elif parsed_assertion is None or parsed_expected is None or parsed_assertion != parsed_expected:
            date_mismatches.append(expected_id)
    medication_missing, medication_mismatches = [], []
    for expected_id in golden.get("required_medication_ids", []):
        assertion = next((item for item in audit.medication_assertions if expected_id in item.evidence_ids), None)
        fact = records.get(expected_id)
        if assertion is None or fact is None:
            medication_missing.append(expected_id)
            continue
        if _norm(assertion.raw) != _norm(fact.get("raw")):
            medication_mismatches.append(expected_id)
            continue
        for field in ("canonical", "schedule"):
            fact_value = fact.get(field)
            assertion_value = getattr(assertion, field)
            if fact_value is None:
                continue
            if _norm(assertion_value) != _norm(fact_value):
                medication_mismatches.append(expected_id)
                break
    contradiction_groups = [set(group) for group in golden.get("required_contradiction_groups", [])]
    matched_contradictions = sum(
        1 for group in contradiction_groups
        if any(group <= set(item.evidence_ids) for item in audit.contradictions)
    )
    with_valid_evidence = sum(1 for item in audit.findings if item.evidence_ids and set(item.evidence_ids) <= records.keys())
    score = {
        "passed": not (
            invalid_ids or lab_missing or lab_value_mismatches or date_missing or date_mismatches
            or medication_missing or medication_mismatches
            or matched_contradictions < len(contradiction_groups)
            or with_valid_evidence < golden.get("required_finding_evidence", 0)
        ),
        "provenance": {"invalid_ids": invalid_ids, "total_ids": len(_assertion_ids(audit))},
        "labs": {"matched": len(golden.get("required_lab_ids", [])) - len(lab_missing) - len(lab_value_mismatches), "missing": lab_missing, "value_mismatches": lab_value_mismatches},
        "dates": {"matched": len(golden.get("required_date_ids", [])) - len(date_missing) - len(date_mismatches), "missing": date_missing, "mismatches": date_mismatches},
        "medications": {"matched": len(golden.get("required_medication_ids", [])) - len(medication_missing) - len(medication_mismatches), "missing": medication_missing, "mismatches": medication_mismatches},
        "contradictions": {"matched": matched_contradictions, "expected": len(contradiction_groups)},
        "findings": {"with_valid_evidence": with_valid_evidence, "required": golden.get("required_finding_evidence", 0)},
    }
    return score


def build_audit_prompts(package: dict) -> tuple[str, str]:
    user = f"""FACT_PACKAGE_SHA256: {package['sha256']}

FACT_PACKAGE:
{_package_json(package)}

Верни JSON строго этой структуры, без Markdown и без дополнительных ключей:
{{
  "lab_assertions": [{{"evidence_ids": ["LAB:..."], "name": "", "value_num": 0, "unit": "", "status": "low|high|normal|unknown"}}],
  "date_assertions": [{{"evidence_ids": ["REP:..."], "date": "YYYY-MM-DD"}}],
  "medication_assertions": [{{"evidence_ids": ["MED:..."], "raw": "", "canonical": "", "schedule": ""}}],
  "contradictions": [{{"evidence_ids": ["...", "..."], "description": ""}}],
  "findings": [{{"type": "FACT|ИНТЕРПРЕТАЦИЯ|ГИПОТЕЗА", "text": "", "evidence_ids": ["..."], "confidence": "high|medium|low"}}],
  "missing_data": []
}}
Каждый evidence_id должен буквально существовать в FACT_PACKAGE. Не используй пустые evidence_ids
для lab_assertions, date_assertions, medication_assertions, contradictions и findings.
"""
    return _AUDIT_SYSTEM, user


def build_synthesis_prompts(package: dict) -> tuple[str, str]:
    user = f"""FACT_PACKAGE_SHA256: {package['sha256']}

FACT_PACKAGE:
{_package_json(package)}

Подготовь отчёт с разделами: резюме и приоритеты; динамика лабораторных показателей;
заключения врачей; лекарства; Garmin и активность; межсистемные связи; красные флаги;
неопределённость; недостающие данные; вопросы врачам; план наблюдения; итог.
Для каждого существенного пункта укажи тип, evidence_ids и confidence.
"""
    return _SYNTHESIS_SYSTEM, user


def extract_evidence_ids(text: str) -> set[str]:
    return set(_EVIDENCE_ID_RE.findall(text))


def score_evidence_ids(cited: set[str], valid: set[str]) -> dict:
    valid_citations = cited & valid
    invalid = sorted(cited - valid)
    return {
        "cited": len(cited),
        "valid": len(valid_citations),
        "invalid": invalid,
        "precision": len(valid_citations) / len(cited) if cited else 1.0,
    }


def claim_set_jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def average_pairwise_jaccard(claim_sets: Iterable[set[str]]) -> float:
    sets = list(claim_sets)
    if len(sets) < 2:
        return 1.0
    scores = [
        claim_set_jaccard(sets[left], sets[right])
        for left in range(len(sets))
        for right in range(left + 1, len(sets))
    ]
    return sum(scores) / len(scores)


def _stop_all_models() -> None:
    listing = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=15, check=False)
    for line in listing.stdout.splitlines()[1:]:
        model = line.split(maxsplit=1)[0] if line.split() else ""
        if model:
            subprocess.run(["ollama", "stop", model], capture_output=True, text=True, timeout=60, check=False)


def merge_stream_message(result: dict, content_parts: list[str], thinking_parts: list[str]) -> dict:
    message = result.setdefault("message", {})
    content = "".join(content_parts)
    thinking = "".join(thinking_parts)
    if content:
        message["content"] = content
    if thinking:
        message["thinking"] = thinking
    return result


def _chat(model: str, system: str, user: str, config: dict, seed: int, timeout_s: float) -> tuple[dict, float]:
    payload = {
        "model": model,
        "stream": True,
        "think": config["think"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {
            "temperature": config["temperature"],
            "top_p": 0.9,
            "seed": seed,
            "num_ctx": config["num_ctx"],
            "num_predict": config["num_predict"],
            "keep_alive": "30m",
        },
    }
    if config.get("format_schema"):
        payload["format"] = config["format_schema"]
    request = urllib.request.Request(
        f"http://{os.getenv('OLLAMA_HOST', '127.0.0.1:11434')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    result: dict = {}
    started = time.monotonic()
    last_heartbeat = started
    heartbeat_interval_s = 30.0
    phase = "prompt"
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        for raw_line in response:
            chunk = json.loads(raw_line)
            message = chunk.get("message") or {}
            content = message.get("content") or ""
            thinking = message.get("thinking") or ""
            if thinking:
                phase = "thinking" if not content_parts else "content"
                thinking_parts.append(thinking)
            if content:
                phase = "content"
                content_parts.append(content)
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval_s:
                elapsed = now - started
                output_tokens = chunk.get("eval_count") or 0
                print(
                    f"[LIVE] model={model} phase={phase} elapsed={elapsed:.0f}s "
                    f"content={sum(map(len, content_parts))} chars "
                    f"thinking={sum(map(len, thinking_parts))} chars "
                    f"tokens={output_tokens}",
                    flush=True,
                )
                last_heartbeat = now
            if chunk.get("done"):
                result = chunk
                break
    print(
        f"[DONE] model={model} elapsed={time.monotonic() - started:.1f}s "
        f"content={sum(map(len, content_parts))} chars "
        f"thinking={sum(map(len, thinking_parts))} chars",
        flush=True,
    )
    merge_stream_message(result, content_parts, thinking_parts)
    return result, time.monotonic() - started


def _warmup(model: str, timeout_s: float) -> None:
    _chat(
        model,
        "Отвечай кратко по-русски.",
        "Напиши: готово.",
        {"think": False, "temperature": 0.0, "num_ctx": 2048, "num_predict": 32},
        seed=42,
        timeout_s=timeout_s,
    )


def _thermal_preflight(output_prefix: Path) -> dict:
    with TelemetrySession(output_prefix, interval_s=1.0) as session:
        session.wait_ready()
        time.sleep(60)
    return session.summary()


def _preflight_ok(summary: dict) -> bool:
    cpu = summary.get("metrics", {}).get("cpu_package_temp_c", {})
    return summary.get("sample_count", 0) >= 30 and cpu.get("p95", float("inf")) <= 90.0


def calibrate_model(model: str, output_dir: Path, timeout_s: float) -> list[dict]:
    _stop_all_models()
    model_dir = output_dir / model.replace("/", "_").replace(":", "_")
    model_dir.mkdir(parents=True, exist_ok=True)
    variants = (
        {"name": "short_off_4k", "think": False, "num_ctx": 4096, "num_predict": 256},
        {"name": "short_off_8k", "think": False, "num_ctx": 8192, "num_predict": 256},
        {"name": "short_think_8k", "think": MODEL_CONFIGS[model]["think"], "num_ctx": 8192, "num_predict": 1024},
    )
    results = []
    for variant in variants:
        prefix = model_dir / variant["name"]
        record = {"model": model, **variant, "error": None}
        try:
            with TelemetrySession(prefix, interval_s=1.0) as session:
                session.wait_ready()
                response, elapsed = _chat(
                    model,
                    "Отвечай по-русски кратко и точно.",
                    "Объясни в двух абзацах, чем отличаются абсолютное и относительное значение лабораторного показателя.",
                    {**variant, "temperature": 0.0, "top_p": 0.9},
                    seed=42,
                    timeout_s=timeout_s,
                )
            message = response.get("message") or {}
            content = (message.get("content") or "").strip()
            record.update({
                "wall_s": elapsed,
                "prompt_tokens": response.get("prompt_eval_count", 0),
                "output_tokens": response.get("eval_count", 0),
                "output_chars": len(content),
                "eval_duration_ns": response.get("eval_duration", 0),
                "generation_tps": (
                    response.get("eval_count", 0) / (response.get("eval_duration", 0) / 1e9)
                    if response.get("eval_duration") else None
                ),
                "telemetry": session.summary(),
            })
            prefix.with_suffix(".output.md").write_text(content, encoding="utf-8")
        except Exception as exc:
            record["error"] = str(exc)
        results.append(record)
        atomic_write_json(model_dir / "calibration.json", results)
    _stop_all_models()
    return results


def run_model(
    model: str,
    package: dict,
    output_dir: Path,
    timeout_s: float,
) -> list[RunResult]:
    _stop_all_models()
    model_dir = output_dir / model.replace("/", "_").replace(":", "_")
    model_dir.mkdir(parents=True, exist_ok=True)
    preflight = _thermal_preflight(model_dir / "preflight")
    thermally_constrained = not _preflight_ok(preflight)
    atomic_write_json(model_dir / "preflight.json", {
        "thermally_constrained": thermally_constrained,
        "telemetry": preflight,
    })
    _warmup(model, timeout_s)
    valid_ids = all_fact_ids(package)
    scenarios = [("audit", *build_audit_prompts(package), 42, 0.0, False)] + [
        ("synthesis", *build_synthesis_prompts(package), seed, 0.2, True)
        for seed in (42, 43, 44)
    ]
    results = []
    for scenario, system, user, seed, temperature, think_enabled in scenarios:
        config = {
            "think": MODEL_CONFIGS[model]["think"] if think_enabled else False,
            "temperature": temperature,
            "num_ctx": 32768,
            "num_predict": 16384,
        }
        prefix = model_dir / f"{scenario}_seed_{seed}"
        result = RunResult(model, scenario, seed, 0.0, 0, 0, 0, 0, {}, {}, thermally_constrained)
        try:
            with TelemetrySession(prefix, interval_s=1.0) as session:
                session.wait_ready()
                response, elapsed = _chat(model, system, user, config, seed, timeout_s)
            message = response.get("message") or {}
            content = (message.get("content") or "").strip()
            thinking = (message.get("thinking") or "").strip()
            result.wall_s = elapsed
            result.prompt_tokens = int(response.get("prompt_eval_count") or 0)
            result.output_tokens = int(response.get("eval_count") or 0)
            result.thinking_chars = len(thinking)
            result.output_chars = len(content)
            result.evidence = score_evidence_ids(extract_evidence_ids(content), valid_ids)
            result.telemetry = session.summary()
            (prefix.with_suffix(".output.md")).write_text(content, encoding="utf-8")
            (prefix.with_suffix(".thinking.md")).write_text(thinking, encoding="utf-8")
        except Exception as exc:
            result.error = str(exc)
        results.append(result)
        atomic_write_json(model_dir / "results.json", [asdict(item) for item in results])
    _stop_all_models()
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data") / "botkin.db")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("benchmarks") / "deep_model_benchmark")
    parser.add_argument("--timeout", type=float, default=7200.0)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    package = load_fact_package(args.db, args.user_id)
    atomic_write_json(args.output / "fact_package.json", package)
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "fact_package_sha256": package["sha256"],
        "models": args.models,
    }
    atomic_write_json(args.output / "manifest.json", manifest)
    if args.prepare_only:
        print(json.dumps({"sha256": package["sha256"], "facts": {
            key: len(value) for key, value in package["facts"].items() if isinstance(value, list)
        }}, ensure_ascii=False))
        return 0
    if args.calibrate:
        output = []
        for model in args.models:
            output.extend(calibrate_model(model, args.output / "calibration", args.timeout))
        atomic_write_json(args.output / "calibration.json", output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    all_results = []
    for model in args.models:
        all_results.extend(run_model(model, package, args.output, args.timeout))
        atomic_write_json(args.output / "runs.json", [asdict(item) for item in all_results])
    print(json.dumps([asdict(item) for item in all_results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
