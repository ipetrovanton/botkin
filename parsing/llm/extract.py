import base64
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from backend.contracts import LabResult, Prescription, OCRResult, DoctorReport
from .ollama_client import get_client, LLM_MODEL
from backend.config import (
    VLM_MODEL, TEXT_MODEL, TEXT_TEMP, VLM_TEMP,
    TEXT_NUM_CTX, VLM_NUM_CTX, VLM_NUM_PREDICT,
    TEXT_MAX_TOKENS, VLM_MAX_TOKENS, PDF_SCALE_X, PDF_SCALE_Y, MAX_TEXT_LENGTH, MAX_PAGES
)
import instructor
# PIL Image is not used
import pymupdf
from parsing.ocr.preprocess import has_text_layer

log = logging.getLogger(__name__)

# ============ WRAPPER MODELS (best-practice для instructor) ============

class LabResults(BaseModel):
    """Контейнер для списка анализов."""
    results: list[LabResult] = []

class Prescriptions(BaseModel):
    """Контейнер для списка назначений."""
    results: list[Prescription] = []

class DoctorReports(BaseModel):
    """Контейнер для списка заключений врача."""
    results: list[DoctorReport] = []

# ============ HELPERS ============

def _pdf_to_base64_images(file_path: Path | str) -> list[str]:
    """Конвертирует PDF или изображение в base64-кодированные JPEG-строки."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    b64_images = []
    try:
        if path.suffix.lower() == ".pdf":
            doc = pymupdf.open(str(path))
            mat = pymupdf.Matrix(PDF_SCALE_X, PDF_SCALE_Y)
            for i, page in enumerate(doc):
                if i >= MAX_PAGES:
                    log.warning(f"PDF has more than {MAX_PAGES} pages, processing only first {MAX_PAGES}")
                    break
                pix = page.get_pixmap(matrix=mat)
                b64_images.append(base64.b64encode(pix.tobytes("jpeg")).decode("utf-8"))
            doc.close()
        else:
            with open(path, "rb") as f:
                b64_images.append(base64.b64encode(f.read()).decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to convert {path} to base64: {e}") from e

    return b64_images

def _extract_pdf_text(pdf_path: Path) -> str:
    """Извлекает текст из PDF с текстовым слоем."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(pdf_path))
        text_parts = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        return "\n\n".join(text_parts)
    except Exception as e:
        raise RuntimeError(f"Failed to extract text from {pdf_path}: {e}") from e


# ============ ANALYSIS ============

ANALYSIS_SYSTEM = """Ты — медицинский ассистент, который ТОЧНО извлекает показатели из лабораторных анализов.

Правила:
1. Извлекай ТОЛЬКО реальные показатели с числовыми значениями. Заголовки таблиц — пропускай.
2. Единицы измерения сохраняй как есть: "г/л", "ммоль/л", "%", "×10⁹/л" (или "10^9/L").
3. Если в ячейке диапазон ("4.0-5.5") — это референс, не value. Заполни ref_low=4.0, ref_high=5.5.
4. analyte_name — на русском, как в документе. analyte_code — на английском (HGB, RBC, GLU), если узнаёшь.
5. taken_at — дата забора крови (если есть в шапке документа). Формат ISO: 2026-05-15T10:30:00.
6. Если поле отсутствует — null (не пустая строка, не 0).
7. ВЕРНИ только JSON массив объектов, без пояснений."""

ANALYSIS_FEWSHOT = """Пример входа:
Гемоглобин (HGB)  145 г/л    Норма: 120-160
Эритроциты       4.8         3.9-4.7  ×10^12/L
Холестерин общий 6.2 ммоль/л  < 5.2

Пример выхода:
[
  {"analyte_code": "HGB", "analyte_name": "Гемоглобин", "value_num": 145.0, "value_text": null,
   "unit": "г/л", "ref_low": 120.0, "ref_high": 160.0, "taken_at": null, "source_table_cell": null},
  {"analyte_code": "RBC", "analyte_name": "Эритроциты", "value_num": 4.8, "value_text": null,
   "unit": "×10^12/L", "ref_low": 3.9, "ref_high": 4.7, "taken_at": null, "source_table_cell": null},
  {"analyte_code": null, "analyte_name": "Холестерин общий", "value_num": 6.2, "value_text": null,
   "unit": "ммоль/л", "ref_low": null, "ref_high": 5.2, "taken_at": null, "source_table_cell": null}
]"""

ANALYSIS_VLM_SYSTEM = """Ты — клинический ассистент, который читает фото и сканы русскоязычных медицинских документов.

ШАГ 1 — РАСПОЗНАВАНИЕ:
Прочитай документ ДОСЛОВНО. Сохраняй:
- Все цифры с теми же десятичными разделителями (запятая или точка) как в оригинале.
- Все единицы измерения (г/л, г/дл, ×10^9/л, тыс/мкл, мкмоль/л и т.д.).
- Все флаги «*», «↑», «↓», «(+)», «++», «+++».
- Все референсные интервалы как они написаны ("0.34 - 4.9", "< 35", "> 1.2", "не обнаружено").
- Категории качественных тестов ОАМ: «не обнаружено», «+», «++», «оксалаты +».

ШАГ 2 — СТРУКТУРИРОВАНИЕ:
По распознанному тексту собери JSON массив объектов.
Если в документе есть дата забора, проставь её в taken_at.
analyte_name — на русском (как в бланке), analyte_code — на английском, если знаешь.

ВЕРНИ ТОЛЬКО JSON МАССИВ.
ВНИМАНИЕ: Запрещено писать размышления (thinking/reasoning), объяснения или вводный текст. Сразу начинай генерацию JSON в Markdown-блоке. Никаких рассуждений до JSON!"""

def run_analysis(ocr: OCRResult | None = None, source_path: Path | None = None) -> list[LabResult]:
    """Извлекает лабораторные показатели из документа."""
    if source_path and source_path.exists():
        if has_text_layer(source_path):
            text = _extract_pdf_text(source_path)
            client = get_client(temperature=TEXT_TEMP, mode=instructor.Mode.MD_JSON)
            response = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": ANALYSIS_SYSTEM + "\n\n" + ANALYSIS_FEWSHOT},
                    {"role": "user", "content": f"Извлеки показатели из этого документа:\n\n{text[:MAX_TEXT_LENGTH]}"},
                ],
                response_model=LabResults,
                max_retries=3,
                max_tokens=TEXT_MAX_TOKENS,
                extra_body={"options": {"num_ctx": TEXT_NUM_CTX}}
            )
            return response.results
        else:
            client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.MD_JSON)
            b64_images = _pdf_to_base64_images(source_path)
            content = [{"type": "text", "text": "Extract lab results from these document images."}]
            for b64 in b64_images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

            messages = [
                {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
                {"role": "user", "content": content}
            ]
            response = client.chat.completions.create(
                model=VLM_MODEL,
                messages=messages,
                response_model=LabResults,
                max_retries=2,
                max_tokens=VLM_MAX_TOKENS,
                extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT}}
            )
            return response.results

    if ocr is None:
        raise ValueError("Either source_path or ocr result must be provided")

    client = get_client(temperature=TEXT_TEMP, mode=instructor.Mode.MD_JSON)
    content = ocr.tables_markdown[0] if ocr.tables_markdown else ocr.text
    response = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {"role": "system", "content": ANALYSIS_SYSTEM + "\n\n" + ANALYSIS_FEWSHOT},
            {"role": "user", "content": f"Извлеки показатели из этого документа:\n\n{content[:MAX_TEXT_LENGTH]}"},
        ],
        response_model=LabResults,
        max_retries=3,
        max_tokens=TEXT_MAX_TOKENS,
        extra_body={"options": {"num_ctx": TEXT_NUM_CTX}}
    )
    return response.results

# ============ PRESCRIPTION ============

PRESCRIPTION_SYSTEM = """Ты — медицинский ассистент, который извлекает назначения лекарств.

Правила:
1. Для каждого препарата извлеки: МНН (международное непатентованное наименование) и торговое название.
2. МНН — обязательно на русском в нижнем регистре (например, "аторвастатин", не "Аторвастатин" и не "Atorvastatin").
3. Если в рецепте указано только торговое название — попробуй определить МНН по знаниям.
4. dose — строка с единицей: "20 мг", "500 мг", "1 капля".
5. frequency — строка: "1 раз в день вечером", "2 раза в день после еды".
6. duration_days — число дней, если указано. Если "30 дней" → 30. Если "1 месяц" → 30. Если не указано → null.
7. doctor_name — ФИО врача, если есть. Иначе null.
8. ВЕРНИ только JSON массив объектов."""

PRESCRIPTION_FEWSHOT = """Пример входа:
Липримар 20 мг — по 1 таблетке вечером после ужина, в течение 30 дней
Конкор 5 мг — 1 раз в день утром, длительно

Пример выхода:
[
  {"drug_mnn": "аторвастатин", "drug_trade": "Липримар", "dose": "20 мг",
   "frequency": "1 раз в день вечером после ужина", "duration_days": 30,
   "prescribed_at": null, "doctor_name": null, "form_107_1u_flag": false},
  {"drug_mnn": "бисопролол", "drug_trade": "Конкор", "dose": "5 мг",
   "frequency": "1 раз в день утром", "duration_days": null,
   "prescribed_at": null, "doctor_name": null, "form_107_1u_flag": false}
]"""

PRESCRIPTION_VLM_SYSTEM = """Ты — медицинский ассистент, который извлекает назначения лекарств по фото и сканам.

Правила:
1. Для каждого препарата извлеки: МНН (международное непатентованное наименование) и торговое название.
2. МНН — обязательно на русском в нижнем регистре.
3. Если в рецепте указано только торговое название — попробуй определить МНН по знаниям.
4. dose — строка с единицей: "20 мг", "500 мг", "1 капля".
5. frequency — строка: "1 раз в день вечером", "2 раза в день после еды".
6. duration_days — число дней, если указано. Если "30 дней" → 30. Если "1 месяц" → 30. Если не указано → null.
7. doctor_name — ФИО врача, если есть. Иначе null.
8. ВЕРНИ только JSON массив объектов.
ВНИМАНИЕ: Запрещено писать размышления (thinking/reasoning), объяснения или вводный текст. Сразу начинай генерацию JSON в Markdown-блоке. Никаких рассуждений до JSON!"""

def run_prescription(ocr: OCRResult, source_path: Path | None = None) -> list[Prescription]:
    """Извлекает назначения лекарств из документа."""
    if source_path and source_path.exists():
        if has_text_layer(source_path):
            text = _extract_pdf_text(source_path)
            client = get_client(temperature=TEXT_TEMP, mode=instructor.Mode.MD_JSON)
            response = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": PRESCRIPTION_SYSTEM + "\n\n" + PRESCRIPTION_FEWSHOT},
                    {"role": "user", "content": f"Извлеки назначения:\n\n{text[:MAX_TEXT_LENGTH]}"},
                ],
                response_model=Prescriptions,
                max_retries=3,
                max_tokens=TEXT_MAX_TOKENS,
                extra_body={"options": {"num_ctx": TEXT_NUM_CTX}}
            )
            return response.results
        else:
            client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.MD_JSON)
            b64_images = _pdf_to_base64_images(source_path)
            content = [{"type": "text", "text": "Extract prescriptions from these document images."}]
            for b64 in b64_images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

            messages = [
                {"role": "system", "content": PRESCRIPTION_VLM_SYSTEM},
                {"role": "user", "content": content}
            ]
            response = client.chat.completions.create(
                model=VLM_MODEL,
                messages=messages,
                response_model=Prescriptions,
                max_retries=2,
                max_tokens=VLM_MAX_TOKENS,
                extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT}}
            )
            return response.results

    if ocr is None:
        raise ValueError("Either source_path or ocr result must be provided")

    client = get_client(temperature=TEXT_TEMP, mode=instructor.Mode.MD_JSON)
    response = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {"role": "system", "content": PRESCRIPTION_SYSTEM + "\n\n" + PRESCRIPTION_FEWSHOT},
            {"role": "user", "content": f"Извлеки назначения:\n\n{ocr.text[:MAX_TEXT_LENGTH]}"},
        ],
        response_model=Prescriptions,
        max_retries=3,
        max_tokens=TEXT_MAX_TOKENS,
        extra_body={"options": {"num_ctx": TEXT_NUM_CTX}}
    )
    return response.results

# ============ DOCTOR REPORT ============

DOCTOR_REPORT_SYSTEM = """Ты — медицинский ассистент, который извлекает структурированную информацию из заключений врача.

Правила:
1. diagnosis — основной диагноз (если есть)
2. recommendations — список рекомендаций врача по режиму, диете, образу жизни
3. complaints — список жалоб пациента (если есть)
4. anamnesis — анамнез заболевания (если есть)
5. visit_date — дата визита (если есть в документе)
6. doctor_name — ФИО врача (если есть)
7. department — отделение (если есть)
8. medications — список назначенных препаратов (названия препаратов, дозировки, кратность приёма)
9. ВЕРНИ только JSON массив объектов."""

DOCTOR_REPORT_FEWSHOT = """Пример входа:
Пациент обратился с жалобами на головную боль и слабость. Диагноз: артериальная гипертензия.
Рекомендации: ограничить потребление соли, контролировать АД 2 раза в день, принимать назначенные препараты.
Осмотр: кардиолога, 15.05.2026

Пример выхода:
[
  {"diagnosis": "артериальная гипертензия", "recommendations": ["ограничить потребление соли", "контролировать АД 2 раза в день", "принимать назначенные препараты"], "complaints": ["головная боль", "слабость"], "anamnesis": null, "visit_date": "2026-05-15", "doctor_name": null, "department": "кардиологии", "medications": ["аторвастатин 20 мг 1 раз в день вечером", "бисопролол 5 мг 1 раз в день утром"]}
]"""

DOCTOR_REPORT_VLM_SYSTEM = """Ты — медицинский ассистент, который читает фото и сканы заключений врача.

Правила:
1. diagnosis — основной диагноз (если есть)
2. recommendations — список рекомендаций врача по режиму, диете, образу жизни
3. complaints — список жалоб пациента (если есть)
4. anamnesis — анамнез заболевания (если есть)
5. visit_date — дата визита (если есть в документе)
6. doctor_name — ФИО врача (если есть)
7. department — отделение (если есть)
8. medications — список назначенных препаратов (названия препаратов, дозировки, кратность приёма)
9. ВЕРНИ ТОЛЬКО JSON МАССИВ.
ВНИМАНИЕ: Запрещено писать размышления. Сразу начинай генерацию JSON."""

def run_doctor_report(ocr: OCRResult, source_path: Path | None = None) -> list[DoctorReport]:
    """Извлекает заключения врача из документа."""
    if source_path and source_path.exists():
        if has_text_layer(source_path):
            text = _extract_pdf_text(source_path)
            client = get_client(temperature=TEXT_TEMP, mode=instructor.Mode.MD_JSON)
            response = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": DOCTOR_REPORT_SYSTEM + "\n\n" + DOCTOR_REPORT_FEWSHOT},
                    {"role": "user", "content": f"Извлеки заключение врача:\n\n{text[:MAX_TEXT_LENGTH]}"},
                ],
                response_model=DoctorReports,
                max_retries=3,
                max_tokens=TEXT_MAX_TOKENS,
                extra_body={"options": {"num_ctx": TEXT_NUM_CTX}}
            )
            return response.results
        else:
            client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.MD_JSON)
            b64_images = _pdf_to_base64_images(source_path)
            content = [{"type": "text", "text": "Extract doctor's report from these document images."}]
            for b64 in b64_images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

            messages = [
                {"role": "system", "content": DOCTOR_REPORT_VLM_SYSTEM},
                {"role": "user", "content": content}
            ]
            response = client.chat.completions.create(
                model=VLM_MODEL,
                messages=messages,
                response_model=DoctorReports,
                max_retries=2,
                max_tokens=VLM_MAX_TOKENS,
                extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT}}
            )
            return response.results

    if ocr is None:
        raise ValueError("Either source_path or ocr result must be provided")

    client = get_client(temperature=TEXT_TEMP, mode=instructor.Mode.MD_JSON)
    response = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {"role": "system", "content": DOCTOR_REPORT_SYSTEM + "\n\n" + DOCTOR_REPORT_FEWSHOT},
            {"role": "user", "content": f"Извлеки заключение врача:\n\n{ocr.text[:MAX_TEXT_LENGTH]}"},
        ],
        response_model=DoctorReports,
        max_retries=3,
        max_tokens=TEXT_MAX_TOKENS,
        extra_body={"options": {"num_ctx": TEXT_NUM_CTX}}
    )
    return response.results