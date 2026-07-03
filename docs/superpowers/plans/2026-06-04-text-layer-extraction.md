# Извлечение из текстового слоя PDF — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Для PDF с годным текстовым слоем извлекать лабораторные показатели из точного текста (qwen3-vl без картинки, temp=0) с verbatim-стражем, оставив VLM-over-image фолбэком для сканов/фото.

**Architecture:** В `extract.run_analysis` добавляется гейт: если файл — PDF с годным текстовым слоем, слова реконструируются в физические строки по координатам (детерминированно, `pymupdf`), строки размечает по колонкам text-only LLM-вызов, результат проходит verbatim-страж (каждое число обязано быть в исходном тексте). Слабый результат (0 строк / >50% выбраковки / непарсимый JSON) или отсутствие слоя → существующий VLM-over-image путь. Маппинг сырого ответа переиспользует готовые `rows_from_raw`/`harvest_lab_rows`.

**Tech Stack:** Python, pymupdf, instructor+OpenAI(Ollama), pydantic, pytest.

**Команды окружения:**
- Тесты: `.venv/bin/python -m pytest`
- Один тест: `.venv/bin/python -m pytest tests/test_pdf_text.py::test_name -v`

---

## Файловая структура

| Файл | Ответственность | Действие |
|---|---|---|
| `src/botkin/config.py` | Пороги текстового слоя | Modify (+3 константы) |
| `src/botkin/preprocess/pdf_text.py` | Чистый PDF-слой: реконструкция строк, годность, плоский текст | Create |
| `src/botkin/llm/prompts.py` | Системный промпт структурирования текста | Modify (+ `ANALYSIS_TEXT_SYSTEM`) |
| `src/botkin/llm/extract.py` | verbatim-страж, text-вызов, гейт в `run_analysis` | Modify |
| `tests/conftest.py` | Хелпер сборки PDF-фикстур через pymupdf | Create-or-Modify |
| `tests/test_pdf_text.py` | Тесты `pdf_text.py` | Create |
| `tests/test_verbatim_guard.py` | Тесты verbatim-стража | Create |
| `tests/test_text_layer_extract.py` | Тесты `_structure_text` и гейта `run_analysis` | Create |

---

## Task 1: Пороги в config

**Files:**
- Modify: `src/botkin/config.py` (после строки `VLM_REQUEST_TIMEOUT = ...`, ~117)

- [ ] **Step 1: Добавить три константы**

В `src/botkin/config.py` после блока VLM-констант добавить:

```python
# ── Текстовый слой PDF (детерминированное извлечение без VLM) ────────────────
# Минимум символов на страницу, чтобы считать слой годным (отсекает PDF-сканы
# с пустым/мусорным текстовым слоем).
TEXT_LAYER_MIN_CHARS_PER_PAGE = int(os.getenv("TEXT_LAYER_MIN_CHARS_PER_PAGE", "50"))
# Толеранция по Y (в пунктах) при кластеризации слов в физические строки:
# значение часто сидит на 1px ниже имени, наивное округление разрывает строку.
TEXT_LAYER_Y_TOLERANCE = float(os.getenv("TEXT_LAYER_Y_TOLERANCE", "3.0"))
# Доля забракованных verbatim-стражем чисел, выше которой результат считается
# недостоверным → фолбэк на VLM.
VERBATIM_MAX_REJECT_RATIO = float(os.getenv("VERBATIM_MAX_REJECT_RATIO", "0.5"))
```

- [ ] **Step 2: Проверить импортируемость**

Run: `.venv/bin/python -c "from botkin.config import TEXT_LAYER_MIN_CHARS_PER_PAGE, TEXT_LAYER_Y_TOLERANCE, VERBATIM_MAX_REJECT_RATIO; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/botkin/config.py
git commit -m "feat(config): пороги текстового слоя PDF"
```

---

## Task 2: Хелпер PDF-фикстур

Тесты не должны зависеть от gitignore'нутого `sample_020.pdf`. Строим синтетические PDF через pymupdf в `tmp_path`.

**Files:**
- Create-or-Modify: `tests/conftest.py`

- [ ] **Step 1: Добавить фикстуру-фабрику**

Если `tests/conftest.py` не существует — создать с этим содержимым; если существует — дописать функцию и фикстуру.

```python
import pymupdf
import pytest


def _make_pdf(path, words, *, page_size=(595, 842)):
    """Строит PDF: words — список (x, y, text) на одной странице.

    y — координата baseline в пунктах. Пустой words → страница без текстового слоя.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    for x, y, text in words:
        page.insert_text((x, y), text, fontsize=10)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def make_pdf():
    return _make_pdf
```

- [ ] **Step 2: Проверить, что фикстура собирается**

Run: `.venv/bin/python -c "import pymupdf; d=pymupdf.open(); p=d.new_page(); p.insert_text((50,100),'тест',fontsize=10); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: хелпер сборки PDF-фикстур"
```

---

## Task 3: reconstruct_lines (координатная сборка строк)

**Files:**
- Create: `src/botkin/preprocess/pdf_text.py`
- Test: `tests/test_pdf_text.py`

- [ ] **Step 1: Написать падающий тест**

`tests/test_pdf_text.py`:

```python
from botkin.preprocess.pdf_text import reconstruct_lines


def test_reconstruct_merges_value_offset_by_one_px(make_pdf, tmp_path):
    # Значение сидит на 1pt ниже имени (реальный кейс ИНВИТРО) — Y-толеранция
    # должна слить их в одну физическую строку.
    pdf = tmp_path / "hb.pdf"
    make_pdf(pdf, [
        (50, 100, "Гемоглобин"),
        (200, 101, "13.7"),
        (260, 100, "г/дл"),
        (320, 100, "11.7 - 15.5"),
        (50, 130, "Эритроциты"),
        (200, 130, "4.64"),
        (260, 130, "млн/мкл"),
        (320, 130, "3.8 - 5.1"),
    ])
    lines = reconstruct_lines(pdf)
    hb = [ln for ln in lines if "Гемоглобин" in ln]
    assert len(hb) == 1
    assert "13.7" in hb[0] and "г/дл" in hb[0] and "11.7" in hb[0] and "15.5" in hb[0]
    # Две физические строки показателей (плюс, возможно, пустых нет).
    assert sum(1 for ln in lines if "Эритроциты" in ln) == 1
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_pdf_text.py::test_reconstruct_merges_value_offset_by_one_px -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'botkin.preprocess.pdf_text'`

- [ ] **Step 3: Реализовать модуль**

`src/botkin/preprocess/pdf_text.py`:

```python
"""Извлечение строк из текстового слоя PDF без VLM.

Цифровые PDF (ИНВИТРО и т.п.) несут точный текстовый слой: значения дословно,
с десятичными и правильными единицами. Сборка слов в физические строки —
детерминированная (кластеризация по координате Y с толеранцией: значение часто
сидит на 1px ниже имени показателя, наивное округление разрывает строку).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from botkin.config import TEXT_LAYER_MIN_CHARS_PER_PAGE, TEXT_LAYER_Y_TOLERANCE

log = logging.getLogger(__name__)


def _page_lines(page, y_tol: float) -> list[str]:
    """Слова страницы → физические строки (кластеризация по Y, сортировка по X)."""
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return []
    words = sorted(words, key=lambda w: (w[1], w[0]))  # по y0, затем x0
    clusters: list[tuple[float, list]] = []  # (опорный y0, слова)
    for w in words:
        y0 = w[1]
        if clusters and abs(y0 - clusters[-1][0]) <= y_tol:
            clusters[-1][1].append(w)
        else:
            clusters.append((y0, [w]))
    lines = []
    for _y, group in clusters:
        ordered = sorted(group, key=lambda w: w[0])
        lines.append(" ".join(w[4] for w in ordered).strip())
    return [ln for ln in lines if ln]


def reconstruct_lines(path: Path, y_tol: float | None = None) -> list[str]:
    """Все страницы PDF → список физических строк в порядке документа."""
    tol = TEXT_LAYER_Y_TOLERANCE if y_tol is None else y_tol
    out: list[str] = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            out.extend(_page_lines(page, tol))
    return out
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_pdf_text.py::test_reconstruct_merges_value_offset_by_one_px -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/botkin/preprocess/pdf_text.py tests/test_pdf_text.py
git commit -m "feat(pdf_text): координатная сборка строк текстового слоя"
```

---

## Task 4: has_usable_text_layer + source_text

**Files:**
- Modify: `src/botkin/preprocess/pdf_text.py`
- Test: `tests/test_pdf_text.py`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_pdf_text.py`:

```python
from botkin.preprocess.pdf_text import has_usable_text_layer, source_text


def test_usable_true_for_text_pdf(make_pdf, tmp_path):
    pdf = tmp_path / "t.pdf"
    make_pdf(pdf, [(50, 100, "Гемоглобин"), (200, 100, "13.7"),
                   (50, 130, "Эритроциты"), (200, 130, "4.64"),
                   (50, 160, "Лейкоциты"), (200, 160, "5.15")])
    assert has_usable_text_layer(pdf) is True


def test_usable_false_for_blank_pdf(make_pdf, tmp_path):
    pdf = tmp_path / "blank.pdf"
    make_pdf(pdf, [])  # страница без текстового слоя (скан-подобный)
    assert has_usable_text_layer(pdf) is False


def test_source_text_is_flat_and_normalized(make_pdf, tmp_path):
    pdf = tmp_path / "t.pdf"
    make_pdf(pdf, [(50, 100, "Гемоглобин"), (200, 100, "13.7")])
    txt = source_text(pdf)
    assert "Гемоглобин" in txt and "13.7" in txt
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python -m pytest tests/test_pdf_text.py -k "usable or source_text" -v`
Expected: FAIL — `ImportError: cannot import name 'has_usable_text_layer'`

- [ ] **Step 3: Реализовать**

Дописать в `src/botkin/preprocess/pdf_text.py`:

```python
def source_text(path: Path) -> str:
    """Плоский текст слоя всех страниц (для verbatim-стража)."""
    parts: list[str] = []
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    return "\n".join(parts)


def has_usable_text_layer(path: Path) -> bool:
    """True, если у PDF годный текстовый слой: символов/стр ≥ порога и есть цифры."""
    try:
        with pymupdf.open(str(path)) as doc:
            n_pages = doc.page_count or 1
            text = "".join(page.get_text("text") for page in doc)
    except Exception as e:  # pragma: no cover — битый PDF → не годен, упадём в VLM
        log.warning("[TEXTLAYER] не удалось открыть '%s': %s", path.name, e)
        return False
    chars_per_page = len(text.strip()) / n_pages
    has_digit = any(ch.isdigit() for ch in text)
    return chars_per_page >= TEXT_LAYER_MIN_CHARS_PER_PAGE and has_digit
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_pdf_text.py -v`
Expected: PASS (все тесты файла)

- [ ] **Step 5: Commit**

```bash
git add src/botkin/preprocess/pdf_text.py tests/test_pdf_text.py
git commit -m "feat(pdf_text): годность текстового слоя и плоский текст"
```

---

## Task 5: Промпт структурирования текста

**Files:**
- Modify: `src/botkin/llm/prompts.py` (в конец файла)

- [ ] **Step 1: Добавить промпт**

В конец `src/botkin/llm/prompts.py`:

```python
ANALYSIS_TEXT_SYSTEM = """Ты — медицинский ассистент, который структурирует УЖЕ ИЗВЛЕЧЁННЫЙ текст лабораторного бланка.

Тебе дают строки таблицы анализов, по одной физической строке документа. Твоя задача —
разложить каждую строку по колонкам. Текст уже точный: НИЧЕГО не додумывай и не правь.

ЖЕЛЕЗНЫЕ ПРАВИЛА:
1. КОПИРУЙ символы ДОСЛОВНО из данного текста. Не меняй цифры, не переставляй запятые/точки.
2. НЕ конвертируй единицы (если в тексте «г/дл» — пиши «г/дл», не превращай в «г/л»).
3. НЕ заменяй значения и нормы на «типичные»/«канонические». Используй ТОЛЬКО то, что есть в строке.
4. Если в строке нет показателя (заголовок группы, подпись, примечание) — пропусти её.
5. Каждое число в твоём ответе ОБЯЗАНО присутствовать во входном тексте.

Для каждого показателя верни поля:
- parameter — название показателя дословно ("Гемоглобин", "Нейтрофилы, %");
- value — результат дословно, включая флаг «*»/«↑»/«↓», если он в строке ("13.7", "44.6*");
- unit — единица дословно ("г/дл", "%", "тыс/мкл"), иначе null;
- reference_range — референс дословно ("11.7 - 15.5", "< 1.0", "35 - 45"), иначе null;
- comment — примечание из строки, иначе null.

Группируй показатели в tests[].results[] (по заголовкам исследований, если они есть)."""
```

- [ ] **Step 2: Проверить импорт**

Run: `.venv/bin/python -c "from botkin.llm.prompts import ANALYSIS_TEXT_SYSTEM; print(len(ANALYSIS_TEXT_SYSTEM))"`
Expected: число > 0

- [ ] **Step 3: Commit**

```bash
git add src/botkin/llm/prompts.py
git commit -m "feat(prompts): промпт структурирования текстового слоя"
```

---

## Task 6: verbatim-страж

**Files:**
- Modify: `src/botkin/llm/extract.py`
- Test: `tests/test_verbatim_guard.py`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_verbatim_guard.py`:

```python
from botkin.llm.extract import _verbatim_guard
from botkin.domain.models import LabResult

SOURCE = "Гемоглобин 13.7 г/дл 11.7 - 15.5\nЭритроциты 4.64 млн/мкл 3.8 - 5.1"


def test_guard_keeps_row_present_in_source():
    rows = [LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                      ref_low=11.7, ref_high=15.5)]
    kept, rejected = _verbatim_guard(rows, SOURCE)
    assert len(kept) == 1 and len(rejected) == 0


def test_guard_rejects_hallucinated_value():
    # 137 и 120/160 отсутствуют в исходном тексте — галлюцинация-нормализация.
    rows = [LabResult(analyte_name="Гемоглобин", value_num=137.0, value_raw="137",
                      ref_low=120.0, ref_high=160.0)]
    kept, rejected = _verbatim_guard(rows, SOURCE)
    assert len(kept) == 0 and len(rejected) == 1


def test_guard_handles_comma_decimal_and_integer_ref():
    rows = [LabResult(analyte_name="Эритроциты", value_num=4.64, value_raw="4,64",
                      ref_low=3.8, ref_high=5.1)]
    kept, _ = _verbatim_guard(rows, SOURCE)  # 4,64 == 4.64 в источнике
    assert len(kept) == 1
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python -m pytest tests/test_verbatim_guard.py -v`
Expected: FAIL — `ImportError: cannot import name '_verbatim_guard'`

- [ ] **Step 3: Реализовать**

В `src/botkin/llm/extract.py` добавить (рядом с другими хелперами, после `extraction_quality`):

```python
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _num_tokens(*values) -> list[str]:
    """Нормализованные числовые токены из значений (запятая→точка, без хвостовых .0)."""
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        for m in _NUM_RE.findall(str(v)):
            s = m.replace(",", ".")
            if s.endswith(".0"):
                s = s[:-2]
            out.append(s)
    return out


def _verbatim_guard(rows: list[LabResult], source_text: str):
    """Делит строки на (kept, rejected): каждое число строки обязано быть в source_text.

    Числа источника собираем в множество нормализованных токенов; строка проходит,
    если ВСЕ её числа (value_raw + границы референса) присутствуют в источнике.
    """
    source_nums = set(_num_tokens(source_text))
    kept: list[LabResult] = []
    rejected: list[LabResult] = []
    for r in rows:
        tokens = _num_tokens(r.value_raw, r.ref_low, r.ref_high, r.ref_text)
        if all(t in source_nums for t in tokens):
            kept.append(r)
        else:
            rejected.append(r)
    return kept, rejected
```

- [ ] **Step 4: Запустить — убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_verbatim_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/botkin/llm/extract.py tests/test_verbatim_guard.py
git commit -m "feat(extract): verbatim-страж против галлюцинаций значений"
```

---

## Task 7: text-only вызов и _structure_text

**Files:**
- Modify: `src/botkin/llm/extract.py`
- Test: `tests/test_text_layer_extract.py`

- [ ] **Step 1: Написать падающий тест**

`tests/test_text_layer_extract.py`:

```python
import botkin.llm.extract as ex
from botkin.llm.extract import RawAnalysis


def test_structure_text_maps_raw_to_rows(monkeypatch):
    # Модель размечает строки в RawAnalysis; маппинг → LabResult идёт через rows_from_raw.
    raw = RawAnalysis.model_validate({"results": [
        {"parameter": "Гемоглобин", "value": "13.7", "unit": "г/дл",
         "reference_range": "11.7 - 15.5"},
        {"parameter": "Эритроциты", "value": "4.64", "unit": "млн/мкл",
         "reference_range": "3.8 - 5.1"},
    ]})
    monkeypatch.setattr(ex, "_call_text", lambda messages, name: raw)
    rows = ex._structure_text(["Гемоглобин 13.7 г/дл 11.7 - 15.5",
                               "Эритроциты 4.64 млн/мкл 3.8 - 5.1"], "doc.pdf")
    names = [r.analyte_name for r in rows]
    assert names == ["Гемоглобин", "Эритроциты"]
    assert rows[0].unit == "г/дл" and rows[0].value_num == 13.7
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_text_layer_extract.py::test_structure_text_maps_raw_to_rows -v`
Expected: FAIL — `AttributeError: module 'botkin.llm.extract' has no attribute '_call_text'`

- [ ] **Step 3: Реализовать**

В `src/botkin/llm/extract.py`:

(a) Расширить `_call_vlm` опциональным параметром `options` — заменить сигнатуру и строку с `extra_body`:

```python
def _call_vlm(messages: list[dict], response_model: type[BaseModel], doc_name: str,
              doc_type: str, options: dict | None = None) -> BaseModel:
```

и внутри, в `client.chat.completions.create(...)`, заменить
`extra_body={"options": default_options()},` на
`extra_body={"options": options or default_options()},`.

(b) Добавить импорт промпта: в строке `from botkin.llm.prompts import ANALYSIS_VLM_SYSTEM, DOCTOR_REPORT_VLM_SYSTEM` дописать `, ANALYSIS_TEXT_SYSTEM`.

(c) Добавить хелперы (после `_extract_once`):

```python
_TEXT_INSTRUCTION = "Размести эти строки лабораторного бланка по колонкам."


def _messages_from_text(system_prompt: str, instruction: str, text: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{instruction}\n\n{text}"},
    ]


def _call_text(messages: list[dict], doc_name: str) -> RawAnalysis:
    """Детерминированный (temp=0) text-only вызов структурирования."""
    options = {**default_options(), "temperature": 0.0}
    return _call_vlm(messages, RawAnalysis, doc_name, "analysis-text", options=options)


def _structure_text(lines: list[str], doc_name: str) -> list[LabResult]:
    """Координатные строки → LabResult через text-only LLM (temp=0) + маппинг."""
    text = "\n".join(lines)
    messages = _messages_from_text(ANALYSIS_TEXT_SYSTEM, _TEXT_INSTRUCTION, text)
    try:
        raw = _call_text(messages, doc_name)
    except ExtractionError as e:
        objs = _salvage_json_objects(_raw_text_from_exc(e))
        return harvest_lab_rows(objs) if objs else []
    rows = rows_from_raw(raw)
    if rows:
        return rows
    data = _loads_json(_raw_content(raw))
    return harvest_lab_rows(data) if data is not None else []
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_text_layer_extract.py::test_structure_text_maps_raw_to_rows -v`
Expected: PASS

- [ ] **Step 5: Прогнать существующие тесты extract (регрессия `_call_vlm`)**

Run: `.venv/bin/python -m pytest tests/test_extract_multipage.py tests/test_extract_mapping.py -v`
Expected: PASS (старая сигнатура `_call_vlm` совместима — параметр опционален)

- [ ] **Step 6: Commit**

```bash
git add src/botkin/llm/extract.py tests/test_text_layer_extract.py
git commit -m "feat(extract): text-only структурирование строк (temp=0)"
```

---

## Task 8: Гейт в run_analysis

**Files:**
- Modify: `src/botkin/llm/extract.py`
- Test: `tests/test_text_layer_extract.py`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_text_layer_extract.py`:

```python
from pathlib import Path
from botkin.domain.models import LabResult


def test_run_analysis_uses_text_layer_when_strong(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_lines", lambda p: ["Гемоглобин 13.7 г/дл 11.7 - 15.5"])
    monkeypatch.setattr(ex, "source_text", lambda p: "Гемоглобин 13.7 г/дл 11.7 - 15.5")
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="Гемоглобин", value_num=13.7, value_raw="13.7",
                  ref_low=11.7, ref_high=15.5)])
    # VLM-путь не должен вызываться
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: (_ for _ in ()).throw(AssertionError("VLM не должен вызываться")))
    rows = ex.run_analysis(Path("doc.pdf"))
    assert [r.analyte_name for r in rows] == ["Гемоглобин"]


def test_run_analysis_falls_back_when_text_layer_weak(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_lines", lambda p: ["мусор"])
    monkeypatch.setattr(ex, "source_text", lambda p: "мусор")
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [])  # слабо → 0 строк
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1"])
    called = {"vlm": False}

    def fake_extract_once(images, name):
        called["vlm"] = True
        return [LabResult(analyte_name="Глюкоза", value_num=5.0)], 1

    monkeypatch.setattr(ex, "_extract_once", fake_extract_once)
    rows = ex.run_analysis(Path("doc.pdf"))
    assert called["vlm"] is True
    assert [r.analyte_name for r in rows] == ["Глюкоза"]


def test_run_analysis_falls_back_when_guard_rejects_majority(monkeypatch):
    monkeypatch.setattr(ex, "_should_use_text_layer", lambda p: True)
    monkeypatch.setattr(ex, "reconstruct_lines", lambda p: ["x"])
    monkeypatch.setattr(ex, "source_text", lambda p: "тут нет таких чисел 1 2")
    # Обе строки с числами, которых нет в источнике → >50% выбраковки.
    monkeypatch.setattr(ex, "_structure_text", lambda lines, name: [
        LabResult(analyte_name="A", value_num=137.0, value_raw="137"),
        LabResult(analyte_name="B", value_num=999.0, value_raw="999")])
    monkeypatch.setattr(ex, "_prepare_b64", lambda p: ["img1"])
    monkeypatch.setattr(ex, "_extract_once", lambda images, name: ([LabResult(analyte_name="Глюкоза", value_num=5.0)], 1))
    rows = ex.run_analysis(Path("doc.pdf"))
    assert [r.analyte_name for r in rows] == ["Глюкоза"]
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `.venv/bin/python -m pytest tests/test_text_layer_extract.py -k "run_analysis" -v`
Expected: FAIL — `AttributeError: ... '_should_use_text_layer'`

- [ ] **Step 3: Реализовать гейт**

В `src/botkin/llm/extract.py`:

(a) Добавить импорты вверху рядом с `from botkin.preprocess.images import ...`:

```python
from botkin.preprocess.pdf_text import (
    has_usable_text_layer, reconstruct_lines, source_text,
)
from botkin.config import VERBATIM_MAX_REJECT_RATIO
```

(b) Добавить хелперы перед `run_analysis`:

```python
def _should_use_text_layer(source_path: Path) -> bool:
    return source_path.suffix.lower() == ".pdf" and has_usable_text_layer(source_path)


def _extract_from_text_layer(source_path: Path) -> list[LabResult] | None:
    """Извлечение из текстового слоя. None → результат слабый, нужен VLM-фолбэк."""
    lines = reconstruct_lines(source_path)
    src = source_text(source_path)
    log.info(
        "[TEXTLAYER_QUALITY] Doc: '%s' | символов=%d | строк-реконструкции=%d",
        source_path.name, len(src), len(lines),
    )
    if not lines:
        return None
    rows = _structure_text(lines, source_path.name)
    if not rows:
        return None
    kept, rejected = _verbatim_guard(rows, src)
    total = len(kept) + len(rejected)
    log.info(
        "[VERBATIM_GUARD] Doc: '%s' | принято=%d забраковано=%d",
        source_path.name, len(kept), len(rejected),
    )
    if total and len(rejected) / total > VERBATIM_MAX_REJECT_RATIO:
        return None
    return kept or None
```

(c) В начале `run_analysis`, сразу после `t0 = time.perf_counter()`, вставить ветку текстового слоя ПЕРЕД `b64_images = _prepare_b64(...)`:

```python
def run_analysis(source_path: Path) -> list[LabResult]:
    t0 = time.perf_counter()

    if _should_use_text_layer(source_path):
        rows = _extract_from_text_layer(source_path)
        if rows is not None:
            log.info("[EXTRACT_PATH] Doc: '%s' | путь: text_layer", source_path.name)
            return _finish(rows, source_path.name, t0, n_calls=0)
    log.info("[EXTRACT_PATH] Doc: '%s' | путь: vlm", source_path.name)

    b64_images = _prepare_b64(source_path)
    n_pages = len(b64_images)
    ...  # остальной существующий код без изменений, КРОМЕ финала (см. ниже)
```

(d) Вынести финальное логирование в `_finish` (DRY: используют оба пути). Заменить хвост `run_analysis` (блок от `q = extraction_quality(rows)` до `return rows`) на вызов `return _finish(rows, source_path.name, t0, n_calls)` и добавить функцию:

```python
def _finish(rows: list[LabResult], doc_name: str, t0: float, n_calls: int) -> list[LabResult]:
    q = extraction_quality(rows)
    total_s = time.perf_counter() - t0
    log.info(
        "[EXTRACT_MAPPED] Doc: '%s' | строк: %d | VLM-вызовов: %d | всего: %.2fs",
        doc_name, len(rows), n_calls, total_s,
    )
    log.info(
        "[EXTRACT_QUALITY] Doc: '%s' | строк: %d | с числом: %d | с текстом: %d | "
        "с нормой: %d | с единицей: %d",
        doc_name, q["total"], q["with_value_num"], q["with_value_text"],
        q["with_ref"], q["with_unit"],
    )
    return rows
```

- [ ] **Step 4: Запустить новые тесты**

Run: `.venv/bin/python -m pytest tests/test_text_layer_extract.py -v`
Expected: PASS (все)

- [ ] **Step 5: Прогнать весь набор (регрессия)**

Run: `.venv/bin/python -m pytest`
Expected: PASS — все тесты (включая существующие multipage/mapping). Прежнее число тестов + новые.

- [ ] **Step 6: Commit**

```bash
git add src/botkin/llm/extract.py tests/test_text_layer_extract.py
git commit -m "feat(extract): гейт текстового слоя с фолбэком на VLM в run_analysis"
```

---

## Task 9: Живой прогон, метрики, журнал

**Files:**
- Modify: `habr/lab-results-journal.md`

- [ ] **Step 1: Прогнать на реальном sample_020.pdf (если доступен локально)**

Run: `.venv/bin/python -c "from pathlib import Path; from botkin.llm.extract import run_analysis; rows=run_analysis(Path('sample_020.pdf')); print('строк:', len(rows)); [print(r.analyte_name, r.value_raw, r.unit, r.ref_low, r.ref_high) for r in rows]"`
Expected: 21 строка; Гемоглобин `13.7 г/дл 11.7 15.5`; в логах `[EXTRACT_PATH] путь: text_layer`.

- [ ] **Step 2: Проверить детерминизм (два прогона дают идентичный результат)**

Run: `.venv/bin/python -c "from pathlib import Path; from botkin.llm.extract import run_analysis; a=[(r.analyte_name,r.value_raw) for r in run_analysis(Path('sample_020.pdf'))]; b=[(r.analyte_name,r.value_raw) for r in run_analysis(Path('sample_020.pdf'))]; print('идентично:', a==b)"`
Expected: `идентично: True`

- [ ] **Step 3: Записать итерацию 8 в журнал**

В `habr/lab-results-journal.md` добавить раздел: проблема (галлюцинация-нормализация Гемоглобина `137 г/л 120-160` на жадном декодировании; неполнота 14/21; обрыв `"3. "`) → диагноз (сырой ответ VLM уже содержит подмену → корень в VLM-вызове; у PDF есть точный текстовый слой, который пайплайн игнорировал) → решение (координатная сборка строк + text-only структурирование temp=0 + verbatim-страж + гейт фолбэка) → метрики до/после из таблицы спеки с фактическими числами прогона. Отметить вне scope: нормализаторные баги (MCHC→ретикулоцит, ложные unit_mismatch, мусорный analyte_group).

- [ ] **Step 4: Commit**

```bash
git add habr/lab-results-journal.md
git commit -m "docs(habr): итерация 8 — извлечение из текстового слоя PDF"
```

---

## Self-Review (выполнено при написании плана)

- **Spec coverage:** реконструкция строк (Task 3) ✓; годность/фолбэк-гейт (Task 4, 8) ✓; промпт (Task 5); verbatim-страж (Task 6); text-only temp=0 (Task 7); наблюдаемость `[EXTRACT_PATH]/[TEXTLAYER_QUALITY]/[VERBATIM_GUARD]` (Task 8); метрики/журнал (Task 9); classify не трогаем ✓; scope только analysis ✓.
- **Placeholder scan:** нет TBD/«обработай ошибки» — код приведён целиком в каждом шаге.
- **Type consistency:** `reconstruct_lines`/`source_text`/`has_usable_text_layer` — одни имена в pdf_text.py и в импортах extract.py; `_call_text`/`_structure_text`/`_verbatim_guard`/`_finish`/`_should_use_text_layer`/`_extract_from_text_layer` согласованы между задачами; `_call_vlm` получает обратносовместимый `options`.
- **Вне scope (зафиксировано):** нормализаторные баги в `normalize/` и `doctor_report` — отдельные задачи.
