"""Классификатор типа документа: rules-first, LLM-tiebreaker."""
import re
from pathlib import Path
from pydantic import BaseModel
from backend.contracts import ClassifyResult, DocType
from .ollama_client import chat_completion, get_client
from backend.config import VLM_MODEL, VLM_TEMP, VLM_NUM_CTX, VLM_NUM_PREDICT
import instructor

CLASSIFY_VLM_SYSTEM = """Ты — точный классификатор медицинских документов. Твоя задача — определить тип документа по его изображению.

Доступные типы (выбери ОДИН):
- analysis: лабораторный анализ (кровь, моча, биохимия) с показателями и нормами
- prescription: рецепт врача или назначение лекарств
- doctor_report: заключение врача, выписка, осмотр
- certificate: справка медицинская
- unknown: не подходит ни под один из выше

Ответь СТРОГО в формате JSON, без пояснений и размышлений:
{"doc_type": "<один_из_типов>", "confidence": <число 0.0-1.0>}"""

class ClassifySchema(BaseModel):
    doc_type: DocType
    confidence: float

def run_vlm(source_path: Path) -> ClassifyResult:
    # Конвертируем PDF или изображение в base64-кодированные картинки
    from parsing.llm.extract import _pdf_to_base64_images
    b64_images = _pdf_to_base64_images(source_path)
    
    client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.JSON)
    content = [{"type": "text", "text": "Classify this medical document image."}]
    # Для классификации достаточно первой страницы бланка
    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_images[0]}"}})
    
    messages = [
        {"role": "system", "content": CLASSIFY_VLM_SYSTEM},
        {"role": "user", "content": content}
    ]
    
    response = client.chat.completions.create(
        model=VLM_MODEL,
        messages=messages,
        response_model=ClassifySchema,
        max_tokens=500,
        extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT, "repeat_penalty": 1.2}}
    )
    return ClassifyResult(doc_type=response.doc_type, confidence=response.confidence)


# Простые ключевые слова — если совпало больше 3, доверяем rules
KEYWORDS: dict[DocType, list[str]] = {
    "analysis": [
        "анализ", "гемоглобин", "лейкоциты", "холестерин", "глюкоза",
        "результат исследования", "норма", "референс", "ммоль/л", "г/л",
        "Инвитро", "Гемотест", "Хеликс", "КДЛ",
    ],
    "prescription": [
        "рецепт", "р/сут", "мг", "принимать", "курс лечения", "доктор",
        "врач", "форма 107", "1 раз в день", "вечером", "утром",
    ],
    "receipt": [
        "кассовый чек", "итог", "итого", "ккт", "ифнс", "офд", "qr",
        "руб", "₽", "оплачено", "ндс",
    ],
    "doctor_report": [
        "выписной эпикриз", "осмотр", "диагноз", "анамнез",
        "жалобы", "рекомендации",
    ],
}


def _rules_score(text: str) -> dict[DocType, int]:
    lower = text.lower()
    return {dt: sum(1 for kw in kws if kw.lower() in lower) for dt, kws in KEYWORDS.items()}


CLASSIFY_PROMPT = """Ты определяешь тип медицинского документа по его тексту.

Доступные типы (выбери ОДИН):
- analysis: лабораторный анализ (кровь, моча, биохимия) с показателями и нормами
- prescription: рецепт врача или назначение лекарств
- receipt: кассовый чек из аптеки или клиники
- certificate: справка медицинская
- doctor_report: заключение врача, выписка, осмотр
- unknown: не подходит ни под один из выше

Текст документа:
---
{text}
---

Ответь СТРОГО в формате JSON, без пояснений:
{{"doc_type": "<один_из_типов>", "confidence": <число 0.0-1.0>}}
"""


def run(text: str) -> ClassifyResult:
    scores = _rules_score(text)
    best_dt, best_score = max(scores.items(), key=lambda x: x[1])

    # Если rules уверенны (≥4 ключевых слов и преимущество ≥2) — без LLM
    sorted_scores = sorted(scores.values(), reverse=True)
    if best_score >= 4 and (len(sorted_scores) < 2 or best_score - sorted_scores[1] >= 2):
        return ClassifyResult(doc_type=best_dt, confidence=min(0.95, 0.6 + best_score * 0.05))

    # Иначе — LLM-tiebreaker
    response = chat_completion(
        messages=[
            {"role": "system", "content": "Ты — точный классификатор. Отвечай только JSON."},
            {"role": "user", "content": CLASSIFY_PROMPT.format(text=text[:3000])},
        ],
        temperature=0.0,
    )
    import json
    try:
        parsed = json.loads(response.strip().strip("`").strip("json").strip())
        return ClassifyResult(
            doc_type=parsed.get("doc_type", "unknown"),
            confidence=float(parsed.get("confidence", 0.5)),
        )
    except Exception:
        return ClassifyResult(doc_type=best_dt if best_score > 0 else "unknown", confidence=0.3)
