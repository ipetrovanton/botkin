import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from backend.contracts import LabResult, Prescription, OCRResult, DoctorReport
from instructor.exceptions import IncompleteOutputException, InstructorRetryException
from .ollama_client import get_client, get_raw_client, LLM_MODEL
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

JSON_CODE_BLOCK_RE = re.compile(r"```(?:json)?(.*?)```", re.IGNORECASE | re.DOTALL)

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

def _call_vlm_structured(messages: list[dict], response_model, doc_name: str, doc_type: str, source_path: Path | None = None):
    import time
    from backend.orchestrator import _log_to_vlm_file
    
    t0 = time.perf_counter()
    _log_to_vlm_file(f"[START_EXTRACT] Doc: '{doc_name}' | Type: '{doc_type}' | Model: {VLM_MODEL}")
    
    client = get_client(temperature=VLM_TEMP, mode=instructor.Mode.JSON)
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
            messages=messages,
            response_model=response_model,
            max_retries=2,
            max_tokens=VLM_MAX_TOKENS,
            extra_body={"options": {"num_ctx": VLM_NUM_CTX, "num_predict": VLM_NUM_PREDICT, "repeat_penalty": 1.2}}
        )
        elapsed = time.perf_counter() - t0
        raw_resp = response._raw_response
        prompt_tokens = raw_resp.usage.prompt_tokens
        completion_tokens = raw_resp.usage.completion_tokens
        speed = completion_tokens / elapsed if elapsed > 0 else 0.0
        
        log_msg = (
            f"[SUCCESS_EXTRACT] Doc: '{doc_name}' | Type: '{doc_type}' | "
            f"Elapsed: {elapsed:.2f}s | Prompt: {prompt_tokens} t | Completion: {completion_tokens} t | "
            f"Inference Speed: {speed:.1f} t/s"
        )
        log.info(log_msg)
        _log_to_vlm_file(log_msg)
        
        # Save raw JSON text to adjacent .txt files if source_path is provided
        if source_path and source_path.exists():
            try:
                raw_text = raw_resp.choices[0].message.content or ""
                # TXT file right next to the original file
                txt_path = source_path.with_suffix(".txt")
                txt_path.write_text(raw_text, encoding="utf-8")
                
                # Also save to adjacent results directory
                results_dir = source_path.parent / "results"
                results_dir.mkdir(exist_ok=True)
                results_txt_path = results_dir / f"{source_path.stem}_result.txt"
                results_txt_path.write_text(raw_text, encoding="utf-8")
                
                _log_to_vlm_file(f"[TXT_SAVED] Saved raw VLM output to {txt_path} and {results_txt_path}")
            except Exception as e:
                log.error(f"Failed to write txt result files: {e}")
                
        return response
    except Exception as e:
        elapsed = time.perf_counter() - t0
        log_msg = f"[FAILED_EXTRACT] Doc: '{doc_name}' | Type: '{doc_type}' | Elapsed: {elapsed:.2f}s | Error: {e}"
        log.error(log_msg)
        _log_to_vlm_file(log_msg)
        raise e


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


def _extract_json_payload(text: str) -> str:
    if not text:
        return ""
    match = JSON_CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped[0] in "[{" and stripped[-1] in "]}":
        return stripped

    def _slice_between(s: str, open_char: str, close_char: str) -> str | None:
        start = s.find(open_char)
        end = s.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            return s[start:end + 1].strip()
        return None

    candidate = _slice_between(stripped, "[", "]")
    if candidate:
        return candidate
    candidate = _slice_between(stripped, "{", "}")
    if candidate:
        return candidate
    return stripped


def _completion_text(completion) -> str:
    try:
        return completion.choices[0].message.content or ""
    except Exception:
        return ""


def _parse_lab_results_from_text(text: str) -> list[LabResult]:
    payload = _extract_json_payload(text)
    if not payload:
        raise ValueError("LLM вернул пустой ответ без JSON")
    try:
        return LabResults.model_validate_json(payload).results
    except Exception:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as decode_err:
            raise ValueError(f"LLM вернул невалидный JSON: {decode_err}") from decode_err

        if isinstance(data, list):
            wrapper = LabResults(results=[LabResult.model_validate(item) for item in data])
            return wrapper.results

        if isinstance(data, dict):
            if "results" in data and isinstance(data["results"], list):
                wrapper = LabResults.model_validate(data)
                return wrapper.results
            # допускаем формат одного объекта
            wrapper = LabResults(results=[LabResult.model_validate(data)])
            return wrapper.results

        raise ValueError("Неподдерживаемый формат JSON для лабораторных показателей")


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
{
  "results": [
  {"analyte_code": "HGB", "analyte_name": "Гемоглобин", "value_num": 145.0, "value_text": null,
   "unit": "г/л", "ref_low": 120.0, "ref_high": 160.0, "taken_at": null, "source_table_cell": null},
  {"analyte_code": "RBC", "analyte_name": "Эритроциты", "value_num": 4.8, "value_text": null,
   "unit": "×10^12/L", "ref_low": 3.9, "ref_high": 4.7, "taken_at": null, "source_table_cell": null},
  {"analyte_code": null, "analyte_name": "Холестерин общий", "value_num": 6.2, "value_text": null,
   "unit": "ммоль/л", "ref_low": null, "ref_high": 5.2, "taken_at": null, "source_table_cell": null}
  ]
}"""

ANALYSIS_VLM_SYSTEM = (
    ANALYSIS_SYSTEM
    + """

Дополнительные требования для фото и сканов:

ШАГ 1 — РАСПОЗНАВАНИЕ:
Прочитай документ ДОСЛОВНО. Сохраняй:
- Все цифры с теми же десятичными разделителями (запятая или точка) как в оригинале.
- Все единицы измерения (г/л, г/дл, ×10^9/л, тыс/мкл, мкмоль/л и т.д.).
- Все флаги «*», «↑», «↓», «(+)», «++», «+++».
- Все референсные интервалы как они написаны ("0.34 - 4.9", "< 35", "> 1.2", "не обнаружено").
- Категории качественных тестов ОАМ: «не обнаружено», «+», «++», «оксалаты +».

ШАГ 2 — СТРУКТУРИРОВАНИЕ:
- Используй те же поля, что и в правилах выше: analyte_code, analyte_name, value_num, value_text,
  unit, ref_low, ref_high, taken_at, source_table_cell.
- value_num — только число. Если значение текстовое («не обнаружено», «+», «++») или содержит символы,
  перенеси его в value_text и поставь value_num = null.
- Если указан диапазон, раздели его на ref_low / ref_high.
- source_table_cell всегда null (мы не сохраняем сырые строки).
- Если дата забора одна на весь документ, повторяй taken_at для каждой строки.
- analyte_name — на русском (как в бланке), analyte_code — на английском, если знаешь.

ФИНАЛЬНЫЙ ВЫВОД:
- Строго JSON-объект с единственным ключом "results", содержащим список объектов показателей, внутри Markdown-блока ```json ... ```. Первая строка твоего ответа должна быть строго ```json, а последняя — ```.
- Запрещено писать какой-либо текст, комментарии, вводные слова или размышления до или после этого блока! Начни ответ сразу с открывающего тега ```json.
- Если нет показателей, верни пустой список внутри ключа "results" (т.е. {"results": []}).
- Любой другой текст считается ошибкой и приводит к повтору запроса.
ВНИМАНИЕ: Запрещено писать размышления (thinking/reasoning), проговаривать шаги или «Сначала посмотрим». Готовый
JSON сразу внутри Markdown-блока, без преамбулы."""
)

def run_analysis(ocr: OCRResult | None = None, source_path: Path | None = None) -> list[LabResult]:
    """Извлекает лабораторные показатели из документа."""
    if source_path and source_path.exists():
        if False:  # Force visual VLM extraction, ignoring bad/corrupt text layers
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
            b64_images = _pdf_to_base64_images(source_path)
            content = [{"type": "text", "text": "Extract lab results from these document images."}]
            for b64 in b64_images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

            messages = [
                {"role": "system", "content": ANALYSIS_VLM_SYSTEM},
                {"role": "user", "content": content}
            ]
            response = _call_vlm_structured(messages, LabResults, source_path.name, "analysis", source_path)
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
{
  "results": [
  {"drug_mnn": "аторвастатин", "drug_trade": "Липримар", "dose": "20 мг",
   "frequency": "1 раз в день вечером после ужина", "duration_days": 30,
   "prescribed_at": null, "doctor_name": null, "form_107_1u_flag": false},
  {"drug_mnn": "бисопролол", "drug_trade": "Конкор", "dose": "5 мг",
   "frequency": "1 раз в день утром", "duration_days": null,
   "prescribed_at": null, "doctor_name": null, "form_107_1u_flag": false}
  ]
}"""

PRESCRIPTION_VLM_SYSTEM = """Ты — медицинский ассистент, который извлекает назначения лекарств по фото и сканам.

Правила:
1. Для каждого препарата извлеки: МНН (международное непатентованное наименование) и торговое название.
2. МНН — обязательно на русском в нижнем регистре.
3. Если в рецепте указано только торговое название — попробуй определить МНН по знаниям.
4. dose — строка с единицей: "20 мг", "500 мг", "1 капля".
5. frequency — строка: "1 раз в день вечером", "2 раза в день после еды".
6. duration_days — число дней, если указано. Если "30 дней" → 30. Если "1 месяц" → 30. Если не указано → null.
7. doctor_name — ФИО врача, если есть. Иначе null.
8. ВЕРНИ только JSON объект с единственным ключом "results", содержащим список объектов назначений, внутри Markdown-блока ```json ... ```.
ВНИМАНИЕ: Запрещено писать размышления (thinking/reasoning), объяснения или вводный текст. Сразу открывай блок кода ```json и начинай генерацию JSON. Никакого текста до и после блока ```json!"""

def run_prescription(ocr: OCRResult, source_path: Path | None = None) -> list[Prescription]:
    """Извлекает назначения лекарств из документа."""
    if source_path and source_path.exists():
        if False:  # Force visual VLM extraction, ignoring bad/corrupt text layers
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
            b64_images = _pdf_to_base64_images(source_path)
            content = [{"type": "text", "text": "Extract prescriptions from these document images."}]
            for b64 in b64_images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

            messages = [
                {"role": "system", "content": PRESCRIPTION_VLM_SYSTEM},
                {"role": "user", "content": content}
            ]
            response = _call_vlm_structured(messages, Prescriptions, source_path.name, "prescription", source_path)
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
{
  "results": [
  {"diagnosis": "артериальная гипертензия", "recommendations": ["ограничить потребление соли", "контролировать АД 2 раза в день", "принимать назначенные препараты"], "complaints": ["головная боль", "слабость"], "anamnesis": null, "visit_date": "2026-05-15", "doctor_name": null, "department": "кардиологии", "medications": ["аторвастатин 20 мг 1 раз в день вечером", "бисопролол 5 мг 1 раз в день утром"]}
  ]
}"""

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
9. ВЕРНИ только JSON объект с единственным ключом "results", содержащим список объектов заключений врача, внутри Markdown-блока ```json ... ```.
ВНИМАНИЕ: Запрещено писать размышления (thinking/reasoning), объяснения или вводный текст. Сразу открывай блок кода ```json и начинай генерацию JSON. Никакого текста до и после блока ```json!"""

def run_doctor_report(ocr: OCRResult, source_path: Path | None = None) -> list[DoctorReport]:
    """Извлекает заключения врача из документа."""
    if source_path and source_path.exists():
        if False:  # Force visual VLM extraction, ignoring bad/corrupt text layers
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
            b64_images = _pdf_to_base64_images(source_path)
            content = [{"type": "text", "text": "Extract doctor's report from these document images."}]
            for b64 in b64_images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

            messages = [
                {"role": "system", "content": DOCTOR_REPORT_VLM_SYSTEM},
                {"role": "user", "content": content}
            ]
            response = _call_vlm_structured(messages, DoctorReports, source_path.name, "doctor_report", source_path)
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