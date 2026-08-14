from __future__ import annotations

import json
from pydantic import BaseModel, ConfigDict, Field


class GarminMetricAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ids: list[str] = Field(min_length=1)
    metric: str
    date: str
    value_num: float
    unit: str
    sleep_phases: dict[str, float] = Field(...)


class GarminActivityAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ids: list[str] = Field(min_length=1)
    activity_type: str
    date: str
    duration_s: float | None = None
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    calories: float | None = None


class GarminAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[GarminMetricAssertion] = Field(default_factory=list)
    activities: list[GarminActivityAssertion] = Field(default_factory=list)
    summary: str = ""


def validate_garmin_audit(payload: dict) -> GarminAudit:
    return GarminAudit.model_validate(payload)


def garmin_json_schema() -> dict:
    return GarminAudit.model_json_schema()


def build_batches(package: dict, health_batch_size: int = 40) -> list[dict]:
    health = package["facts"].get("health", [])
    activities = package["facts"].get("activities", [])
    batches = []
    for start in range(0, len(health), health_batch_size):
        chunk = health[start:start + health_batch_size]
        batches.append({
            "domain": "metrics",
            "expected_ids": [item["id"] for item in chunk],
            "facts": chunk,
        })
    if activities:
        batches.append({
            "domain": "activities",
            "expected_ids": [item["id"] for item in activities],
            "facts": activities,
        })
    return batches


def build_prompt(package: dict, batch: dict) -> tuple[str, str]:
    system = (
        "Ты извлекаешь только факты Garmin из одного batch. Отвечай строго JSON по schema, "
        "без Markdown и дополнительных ключей. Не интерпретируй и не округляй числа. "
        "Каждая строка обязана содержать evidence_ids с ID из списка ОБЯЗАТЕЛЬНЫЕ IDS. "
        "Не добавляй IDs, которых нет в batch. Для sleep_seconds перенеси все sleep_phases "
        "из value_json."
    )
    if batch["domain"] == "metrics":
        instruction = (
            "Заполни metrics для каждого ID. Используй average как value_num, date из факта, "
            "unit без изменений. activities оставь пустым."
        )
    else:
        instruction = (
            "Заполни activities для каждого ID. Перенеси duration_s, distance_m, avg_hr, "
            "max_hr, calories и дату из started_at. metrics оставь пустым."
        )
    user = (
        f"PATIENT_SCOPE: {package['patient']['patient_key']}\n"
        f"ОБЯЗАТЕЛЬНЫЕ IDS: {json.dumps(batch['expected_ids'], ensure_ascii=False)}\n"
        f"FACTS: {json.dumps(batch['facts'], ensure_ascii=False, sort_keys=True)}\n"
        f"{instruction}\n"
        "Если поле отсутствует в FACTS, верни null, не выдумывай значение."
    )
    return system, user


def merge_audits(audits: list[dict]) -> dict:
    return {
        "metrics": [item for audit in audits for item in audit.get("metrics", [])],
        "activities": [item for audit in audits for item in audit.get("activities", [])],
        "summary": "",
    }


def _records(package: dict) -> dict[str, dict]:
    return {
        item["id"]: item
        for domain in ("health", "activities")
        for item in package["facts"].get(domain, [])
        if item.get("id")
    }


def _close(left: float | None, right: object, tolerance: float = 1e-6) -> bool:
    return left is not None and right is not None and abs(left - float(right)) <= tolerance


def score_garmin_audit(audit: GarminAudit, package: dict) -> dict:
    records = _records(package)
    assertions = [*audit.metrics, *audit.activities]
    cited = {evidence_id for item in assertions for evidence_id in item.evidence_ids}
    invalid_ids = sorted(cited - records.keys())
    metric_missing, metric_mismatches = [], []
    sleep_with_phases = 0
    for fact_id, fact in ((item["id"], item) for item in package["facts"].get("health", [])):
        assertion = next((item for item in audit.metrics if fact_id in item.evidence_ids), None)
        if assertion is None:
            metric_missing.append(fact_id)
            continue
        if (
            assertion.metric != fact.get("metric")
            or assertion.date != fact.get("date")
            or assertion.unit != fact.get("unit")
            or not _close(assertion.value_num, fact.get("average"))
        ):
            metric_mismatches.append(fact_id)
        if fact.get("metric") == "sleep_seconds":
            expected_phases = json.loads(fact["value_json"]) if fact.get("value_json") else {}
            if assertion.sleep_phases and all(_close(assertion.sleep_phases.get(key), value) for key, value in expected_phases.items()):
                sleep_with_phases += 1
    activity_missing, activity_mismatches = [], []
    for fact in package["facts"].get("activities", []):
        fact_id = fact["id"]
        assertion = next((item for item in audit.activities if fact_id in item.evidence_ids), None)
        if assertion is None:
            activity_missing.append(fact_id)
            continue
        expected_date = str(fact.get("started_at") or "")[:10]
        if (
            assertion.activity_type != fact.get("activity_type")
            or assertion.date[:10] != expected_date
            or not _close(assertion.duration_s, fact.get("duration_s"))
            or not _close(assertion.distance_m, fact.get("distance_m"))
            or not _close(assertion.avg_hr, fact.get("avg_hr"))
            or not _close(assertion.max_hr, fact.get("max_hr"))
            or not _close(assertion.calories, fact.get("calories"))
        ):
            activity_mismatches.append(fact_id)
    score = {
        "passed": not (invalid_ids or metric_missing or metric_mismatches or activity_missing or activity_mismatches),
        "provenance": {"cited": len(cited), "invalid_ids": invalid_ids},
        "metrics": {"matched": len(package["facts"].get("health", [])) - len(metric_missing) - len(metric_mismatches), "missing": metric_missing, "value_mismatches": metric_mismatches},
        "sleep": {"expected": sum(item.get("metric") == "sleep_seconds" for item in package["facts"].get("health", [])), "with_phases": sleep_with_phases},
        "activities": {"matched": len(package["facts"].get("activities", [])) - len(activity_missing) - len(activity_mismatches), "missing": activity_missing, "value_mismatches": activity_mismatches},
    }
    return score
