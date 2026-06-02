# Photo Preprocessing (Cycle A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подготавливать изображение лучше перед VLM — апскейлить маленькие Telegram-фото к целевому разрешению, повышать контраст/резкость (CLAHE+unsharp), безопасно выпрямлять наклон, и подсказывать слать документ файлом.

**Architecture:** Расширяем `preprocess/images.py` тремя шагами (deskew → resize(двунаправленный) → enhance) на opencv. `extract` использует апскейл+улучшение+дескью; `classify` остаётся дешёвым (downscale-only). Бот подсказывает про отправку файлом.

**Tech Stack:** Python 3.12, Pillow + pillow-heif, **opencv-python-headless + numpy** (новое), PyMuPDF, pytest. opencv/деск работают на CPU — тестируются здесь; эффект на чтение модели проверяет пользователь на GPU.

> **Окружение:** dev-сервер БЕЗ GPU. VLM не запускать; тесты — только на подготовку изображения (CPU). Модель/латентность проверяет пользователь локально.

**Спек:** `docs/superpowers/specs/2026-06-02-photo-preprocessing-design.md`

---

## Карта файлов

```
src/botkin/
├── config.py                MODIFY  IMAGE_MAX_LONG_SIDE→IMAGE_EXTRACT_LONG_SIDE(2200);
│                            +CLAHE/unsharp/deskew пороги; +PHOTO_LOWRES_WARN
├── preprocess/images.py     MODIFY  +_resize(двунаправленный), +_enhance, +_doc_angle/_deskew;
│                            prepare_images(... upscale/deskew/enhance)
├── preprocess/formats.py    CREATE  sniff_extension/resolve_extension (magic-байты, HEIC)
├── api/routes/upload.py     MODIFY  валидация по содержимому (resolve_extension), верный ext
├── llm/extract.py           MODIFY  prepare_images с upscale+deskew+enhance, IMAGE_EXTRACT_LONG_SIDE
├── llm/classify.py          MODIFY  (без изменений логики; sanity — downscale-only)
├── bot/handlers/upload.py   MODIFY  подсказка «слать файлом» + предупреждение; ext из mime_type
└── bot/handlers/help.py     MODIFY  строка про отправку файлом
pyproject.toml               MODIFY  +opencv-python-headless, +numpy
config.json / config.py      MODIFY  +.heif в allowed_extensions
tests/test_preprocess_images.py  MODIFY  апскейл/даунскейл/enhance/deskew
tests/test_formats.py        CREATE  sniff/resolve magic-байты
tests/test_smoke.py          MODIFY  test_config_imports под новые ключи
```

---

## Task 1: Зависимости и конфигурация

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/botkin/config.py`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Обновить тест конфигурации**

В `tests/test_smoke.py::test_config_imports` заменить блок импортов изображений и ассерты.
Найти строки с `IMAGE_MAX_LONG_SIDE, IMAGE_JPEG_QUALITY, IMAGE_CLASSIFY_LONG_SIDE,` и
`assert IMAGE_MAX_LONG_SIDE > IMAGE_CLASSIFY_LONG_SIDE > 0` и заменить на:

```python
        IMAGE_EXTRACT_LONG_SIDE, IMAGE_JPEG_QUALITY, IMAGE_CLASSIFY_LONG_SIDE,
        IMAGE_CLAHE_CLIP, IMAGE_UNSHARP_AMOUNT,
        IMAGE_DESKEW_MIN_ANGLE, IMAGE_DESKEW_MIN_AREA, IMAGE_DESKEW_MAX_AREA,
        PHOTO_LOWRES_WARN,
```

и (в теле ассертов, на месте старого `assert IMAGE_MAX_LONG_SIDE ...`):

```python
    assert IMAGE_EXTRACT_LONG_SIDE > IMAGE_CLASSIFY_LONG_SIDE > 0
    assert IMAGE_CLAHE_CLIP > 0
    assert IMAGE_UNSHARP_AMOUNT >= 1.0
    assert IMAGE_DESKEW_MIN_ANGLE > 0
    assert 0 < IMAGE_DESKEW_MIN_AREA < IMAGE_DESKEW_MAX_AREA <= 1.0
    assert PHOTO_LOWRES_WARN > 0
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_smoke.py::test_config_imports -q`
Expected: FAIL (ImportError: cannot import name `IMAGE_EXTRACT_LONG_SIDE`).

- [ ] **Step 3: Обновить `pyproject.toml`**

В массив `dependencies` добавить:

```toml
    "opencv-python-headless>=4.10",
    "numpy>=1.26",
```

- [ ] **Step 4: Обновить `config.py`**

В `_DEFAULTS["image"]` заменить блок на:

```python
    "image": {
        "extract_long_side": 2200,
        "jpeg_quality": 90,
        "classify_long_side": 1000,
        "clahe_clip": 2.0,
        "unsharp_amount": 1.5,
        "deskew_min_angle": 3.0,
        "deskew_min_area": 0.40,
        "deskew_max_area": 0.97,
        "lowres_warn": 1500,
    },
```

Заменить секцию `# ── Подготовка изображений ──` на:

```python
# ── Подготовка изображений ────────────────────────────────────────────────────
IMAGE_EXTRACT_LONG_SIDE = int(_get("image.extract_long_side", _DEFAULTS["image"]["extract_long_side"]))
IMAGE_JPEG_QUALITY = int(_get("image.jpeg_quality", _DEFAULTS["image"]["jpeg_quality"]))
IMAGE_CLASSIFY_LONG_SIDE = int(_get("image.classify_long_side", _DEFAULTS["image"]["classify_long_side"]))
IMAGE_CLAHE_CLIP = float(_get("image.clahe_clip", _DEFAULTS["image"]["clahe_clip"]))
IMAGE_UNSHARP_AMOUNT = float(_get("image.unsharp_amount", _DEFAULTS["image"]["unsharp_amount"]))
IMAGE_DESKEW_MIN_ANGLE = float(_get("image.deskew_min_angle", _DEFAULTS["image"]["deskew_min_angle"]))
IMAGE_DESKEW_MIN_AREA = float(_get("image.deskew_min_area", _DEFAULTS["image"]["deskew_min_area"]))
IMAGE_DESKEW_MAX_AREA = float(_get("image.deskew_max_area", _DEFAULTS["image"]["deskew_max_area"]))
PHOTO_LOWRES_WARN = int(_get("image.lowres_warn", _DEFAULTS["image"]["lowres_warn"]))
```

- [ ] **Step 5: Обновить `config.json`**

Заменить строку `"image": {...}` на:

```json
  "image": {
    "extract_long_side": 2200,
    "jpeg_quality": 90,
    "classify_long_side": 1000,
    "clahe_clip": 2.0,
    "unsharp_amount": 1.5,
    "deskew_min_angle": 3.0,
    "deskew_min_area": 0.40,
    "deskew_max_area": 0.97,
    "lowres_warn": 1500
  },
```

- [ ] **Step 6: Установить и проверить**

Run: `uv sync && uv run pytest tests/test_smoke.py::test_config_imports -q`
Expected: PASS. Также `uv run python -c "import cv2, numpy; print(cv2.__version__)"` печатает версию.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/botkin/config.py config.json tests/test_smoke.py
git commit -m "feat(config): пороги препроцессинга (extract_long_side/CLAHE/unsharp/deskew) + opencv/numpy"
```

---

## Task 2: Двунаправленный resize (апскейл маленьких)

**Files:**
- Modify: `src/botkin/preprocess/images.py`
- Test: `tests/test_preprocess_images.py`

- [ ] **Step 1: Заменить тест-файл**

Полностью заменить `tests/test_preprocess_images.py` на:

```python
import io

import numpy as np
import pymupdf
import pytest
from PIL import Image

from botkin.preprocess.images import prepare_images, to_base64_jpegs, _doc_angle


def _make_pdf(tmp_path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i} Гемоглобин 145 г/л")
    path = tmp_path / "doc.pdf"
    doc.save(str(path)); doc.close()
    return path


def _make_png(tmp_path, size):
    Image.new("RGB", size, (255, 255, 255)).save(str(tmp_path / "p.png"))
    return tmp_path / "p.png"


def test_small_photo_upscaled_for_extract(tmp_path):
    path = _make_png(tmp_path, (720, 1280))   # типичное Telegram-фото
    images = prepare_images(path, long_side=2200, upscale=True)
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) == 2200              # апскейл до целевого


def test_large_photo_downscaled(tmp_path):
    path = _make_png(tmp_path, (4000, 3000))
    images = prepare_images(path, long_side=2200, upscale=True)
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) == 2200              # даунскейл до целевого


def test_classify_downscale_only_no_upscale(tmp_path):
    path = _make_png(tmp_path, (720, 1280))
    images = prepare_images(path, long_side=1000, upscale=False)
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) == 1280              # маленькое НЕ растёт (только вниз)


def test_pdf_yields_one_image_per_page(tmp_path):
    images = prepare_images(_make_pdf(tmp_path, 2), long_side=2200, upscale=True)
    assert len(images) == 2
    for raw in images:
        Image.open(io.BytesIO(raw))


def test_to_base64(tmp_path):
    b64 = to_base64_jpegs(prepare_images(_make_png(tmp_path, (800, 600)), long_side=2200))
    assert isinstance(b64, list) and b64 and isinstance(b64[0], str)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_images(tmp_path / "nope.pdf", long_side=2200)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_preprocess_images.py -q`
Expected: FAIL (ImportError `_doc_angle` / иное поведение resize).

- [ ] **Step 3: Переписать `preprocess/images.py`**

Полностью заменить содержимое на (включает Task 2/3/4 — resize, enhance, deskew):

```python
"""Подготовка PDF/изображений к VLM: разрешение, контраст/резкость, выпрямление наклона.

Размер изображения определяет число vision-токенов и качество чтения. Маленькие
Telegram-фото апскейлятся до целевого разрешения; крупные — ужимаются. Контраст/резкость
повышаются (CLAHE + unsharp), наклон фото выпрямляется best-effort (с фолбэком на полный кадр).
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover — окружение без pillow-heif
    pass

from botkin.config import (
    IMAGE_CLAHE_CLIP,
    IMAGE_CLASSIFY_LONG_SIDE,
    IMAGE_DESKEW_MAX_AREA,
    IMAGE_DESKEW_MIN_ANGLE,
    IMAGE_DESKEW_MIN_AREA,
    IMAGE_EXTRACT_LONG_SIDE,
    IMAGE_JPEG_QUALITY,
    IMAGE_UNSHARP_AMOUNT,
    MAX_PAGES,
    PDF_RENDER_DPI,
)


def _resize(img: Image.Image, long_side: int, upscale: bool) -> Image.Image:
    """Приводит длинную сторону к long_side. upscale=True — растит маленькие тоже."""
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    width, height = img.size
    longest = max(width, height)
    if longest > long_side or (upscale and longest < long_side):
        ratio = long_side / longest
        resample = Image.LANCZOS if ratio < 1 else Image.BICUBIC
        img = img.resize((round(width * ratio), round(height * ratio)), resample)
    return img


def _enhance(arr: np.ndarray) -> np.ndarray:
    """CLAHE-контраст по L-каналу + мягкий unsharp. Вход/выход — RGB uint8."""
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    luminance, a, b = cv2.split(lab)
    luminance = cv2.createCLAHE(clipLimit=IMAGE_CLAHE_CLIP, tileGridSize=(8, 8)).apply(luminance)
    out = cv2.cvtColor(cv2.merge((luminance, a, b)), cv2.COLOR_LAB2RGB)
    blur = cv2.GaussianBlur(out, (0, 0), 3)
    return cv2.addWeighted(out, IMAGE_UNSHARP_AMOUNT, blur, 1.0 - IMAGE_UNSHARP_AMOUNT, 0)


def _doc_angle(arr: np.ndarray) -> float | None:
    """Угол наклона документа в градусах [-45,45], если он надёжно определён, иначе None.

    Документ — светлая (низкая насыщенность) область на более насыщенном фоне.
    """
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    _, mask = cv2.threshold(saturation, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((35, 35), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area_fraction = cv2.contourArea(contour) / (arr.shape[0] * arr.shape[1])
    if not (IMAGE_DESKEW_MIN_AREA <= area_fraction <= IMAGE_DESKEW_MAX_AREA):
        return None
    angle = cv2.minAreaRect(contour)[2]
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    if abs(angle) < IMAGE_DESKEW_MIN_ANGLE:
        return None
    return float(angle)


def _deskew(arr: np.ndarray) -> np.ndarray:
    """Выпрямляет наклон, если он надёжно определён; иначе возвращает как есть."""
    angle = _doc_angle(arr)
    if angle is None:
        return arr
    height, width = arr.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        arr, matrix, (width, height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def _encode_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buf.getvalue()


def _process(img: Image.Image, long_side: int, upscale: bool, deskew: bool, enhance: bool) -> bytes:
    if deskew:
        img = Image.fromarray(_deskew(np.asarray(img.convert("RGB"))))
    img = _resize(img, long_side, upscale)
    if enhance:
        img = Image.fromarray(_enhance(np.asarray(img)))
    return _encode_jpeg(img)


def _pdf_pages(path: Path, long_side: int, upscale: bool, enhance: bool) -> list[bytes]:
    out: list[bytes] = []
    doc = pymupdf.open(str(path))
    try:
        for index, page in enumerate(doc):
            if index >= MAX_PAGES:
                break
            pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            out.append(_process(img, long_side, upscale, deskew=False, enhance=enhance))
    finally:
        doc.close()
    return out


def prepare_images(
    file_path: Path | str,
    long_side: int | None = None,
    upscale: bool = False,
    deskew: bool = False,
    enhance: bool = False,
) -> list[bytes]:
    """PDF/изображение → список JPEG-байтов. deskew применяется только к растровым фото."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")

    limit = long_side or IMAGE_EXTRACT_LONG_SIDE
    if path.suffix.lower() == ".pdf":
        return _pdf_pages(path, limit, upscale, enhance)

    with Image.open(path) as img:
        return [_process(img, limit, upscale, deskew, enhance)]


def to_base64_jpegs(images: list[bytes]) -> list[str]:
    return [base64.b64encode(raw).decode("utf-8") for raw in images]
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_preprocess_images.py -q`
Expected: PASS (resize-кейсы и базовые проходят; `_doc_angle` импортируется).

- [ ] **Step 5: Commit**

```bash
git add src/botkin/preprocess/images.py tests/test_preprocess_images.py
git commit -m "feat(preprocess): двунаправленный resize + CLAHE/unsharp + best-effort дескью"
```

---

## Task 3: Тест enhance (без артефактов)

**Files:**
- Test: `tests/test_preprocess_images.py`

- [ ] **Step 1: Добавить тест enhance**

В `tests/test_preprocess_images.py` добавить:

```python
def test_enhance_preserves_size_and_valid_jpeg(tmp_path):
    # фото с текстом → enhance не меняет размеры resize и даёт валидный JPEG
    img = Image.new("RGB", (1000, 1400), (200, 200, 200))
    img.save(str(tmp_path / "g.png"))
    raw = prepare_images(tmp_path / "g.png", long_side=2200, upscale=True, enhance=True)[0]
    out = Image.open(io.BytesIO(raw))
    assert out.mode == "RGB"
    assert max(out.size) == 2200
```

- [ ] **Step 2: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_preprocess_images.py::test_enhance_preserves_size_and_valid_jpeg -q`
Expected: PASS (реализация enhance уже в Task 2).

- [ ] **Step 3: Commit**

```bash
git add tests/test_preprocess_images.py
git commit -m "test(preprocess): enhance сохраняет размеры и даёт валидный JPEG"
```

---

## Task 4: Тест дескью (выпрямление и безопасный no-op)

**Files:**
- Test: `tests/test_preprocess_images.py`

- [ ] **Step 1: Добавить тесты дескью на синтетике**

В `tests/test_preprocess_images.py` добавить:

```python
def _tilted_document(angle_deg):
    # тёмный фон + светлый «документ» ~70% площади, повёрнутый на angle_deg
    canvas = np.full((1000, 800, 3), 30, dtype=np.uint8)   # тёмный
    canvas[150:850, 150:650] = 235                          # светлый прямоугольник
    matrix = cv2.getRotationMatrix2D((400, 500), angle_deg, 1.0)
    return cv2.warpAffine(canvas, matrix, (800, 1000), borderValue=(30, 30, 30))


def test_doc_angle_detects_tilt():
    angle = _doc_angle(_tilted_document(10))
    assert angle is not None and abs(abs(angle) - 10) <= 3   # ~10° с допуском


def test_doc_angle_noop_on_uniform():
    uniform = np.full((1000, 800, 3), 235, dtype=np.uint8)  # «документ» во весь кадр
    assert _doc_angle(uniform) is None                       # площадь > max_area → no-op
```

> Импорт `cv2`/`numpy` в тесте уже есть (добавлены в Task 2 Step 1: `import numpy as np`;
> добавьте `import cv2` в шапку тест-файла, если его там нет).

- [ ] **Step 2: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_preprocess_images.py -k doc_angle -q`
Expected: PASS (реализация `_doc_angle` уже в Task 2).

- [ ] **Step 3: Commit**

```bash
git add tests/test_preprocess_images.py
git commit -m "test(preprocess): дескью выпрямляет наклон и безопасно no-op на ровном"
```

---

## Task 5: Подключить в extract (апскейл+дескью+улучшение)

**Files:**
- Modify: `src/botkin/llm/extract.py`
- Test: `tests/test_llm_calls.py`

- [ ] **Step 1: Дополнить тест extract — проверить флаги препроцессинга**

В `tests/test_llm_calls.py` в `test_extract_analysis_mocked` заменить патч `prepare_images` так,
чтобы проверить вызов с апскейлом/дескью/улучшением. Заменить блок `with patch(...)` на:

```python
    with patch("botkin.llm.extract.get_client", return_value=fake), \
         patch("botkin.llm.extract.prepare_images", return_value=[b"\xff\xd8fakejpeg"]) as prep:
        items = extract.run_analysis(_tiny_pdf(tmp_path))

    _, kwargs = prep.call_args
    from botkin.config import IMAGE_EXTRACT_LONG_SIDE
    assert kwargs.get("long_side") == IMAGE_EXTRACT_LONG_SIDE
    assert kwargs.get("upscale") is True
    assert kwargs.get("deskew") is True
    assert kwargs.get("enhance") is True
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_llm_calls.py::test_extract_analysis_mocked -q`
Expected: FAIL (`prepare_images` сейчас вызывается без этих kwargs).

- [ ] **Step 3: Обновить `extract.py`**

В `src/botkin/llm/extract.py` добавить импорт `IMAGE_EXTRACT_LONG_SIDE` в строку импорта из config:

```python
from botkin.config import VLM_MODEL, VLM_TEMPERATURE, VLM_MAX_TOKENS, IMAGE_EXTRACT_LONG_SIDE
```

И заменить первую строку `_build_messages` (вызов `prepare_images`) на:

```python
    b64_images = to_base64_jpegs(prepare_images(
        source_path,
        long_side=IMAGE_EXTRACT_LONG_SIDE,
        upscale=True, deskew=True, enhance=True,
    ))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_llm_calls.py -q`
Expected: PASS (classify-тест тоже зелёный — он downscale-only, не менялся).

- [ ] **Step 5: Commit**

```bash
git add src/botkin/llm/extract.py tests/test_llm_calls.py
git commit -m "feat(llm): extract подаёт апскейленное+улучшенное+выпрямленное изображение"
```

---

## Task 6: Подсказка в боте «слать файлом»

**Files:**
- Modify: `src/botkin/bot/handlers/upload.py`
- Modify: `src/botkin/bot/handlers/help.py`
- Test: `tests/test_bot_hints.py`

- [ ] **Step 1: Написать падающий тест (чистая функция текста)**

Создать `tests/test_bot_hints.py`:

```python
from botkin.bot.handlers.upload import photo_followup_text


def test_followup_always_has_file_hint():
    text = photo_followup_text(image_long_side=3000)
    assert "файл" in text.lower()


def test_followup_warns_on_lowres():
    text = photo_followup_text(image_long_side=720)   # < PHOTO_LOWRES_WARN
    assert "файл" in text.lower()
    assert "качеств" in text.lower() or "разрешени" in text.lower()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_bot_hints.py -q`
Expected: FAIL (ImportError: `photo_followup_text`).

- [ ] **Step 3: Реализовать в `upload.py`**

В `src/botkin/bot/handlers/upload.py` добавить импорт и функцию (рядом с роутером):

```python
from botkin.config import PHOTO_LOWRES_WARN

_FILE_HINT = (
    "📎 Совет: для лучшего распознавания пришлите документ файлом "
    "(скрепка → Файл), а не фото — так сохранится полное разрешение."
)


def photo_followup_text(image_long_side: int) -> str:
    """Текст-подсказка после приёма фото; при низком разрешении — усиленное предупреждение."""
    if image_long_side < PHOTO_LOWRES_WARN:
        return (
            "⚠️ Фото пришло в низком разрешении — качество распознавания может пострадать.\n"
            + _FILE_HINT
        )
    return _FILE_HINT
```

В обработчике `on_photo` после `await message.answer(f"✅ Документ #{doc_id} принят...")` добавить:

```python
    await message.answer(photo_followup_text(photo.width))
```

(`photo` — это `message.photo[-1]`, у него есть `.width`/`.height`.)

- [ ] **Step 4: Добавить строку в `help.py`**

В `HELP_TEXT` (в `src/botkin/bot/handlers/help.py`) в раздел про загрузку добавить строку:

```
📎 <b>Лучшее качество:</b> присылайте документ <b>файлом</b> (скрепка → Файл), а не фото — сохраняется полное разрешение.
```

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_bot_hints.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/botkin/bot/handlers/upload.py src/botkin/bot/handlers/help.py tests/test_bot_hints.py
git commit -m "feat(bot): подсказка слать документ файлом + предупреждение о низком разрешении фото"
```

---

## Task 7: Приём iPhone-файлов (HEIC) — валидация по содержимому

**Files:**
- Create: `src/botkin/preprocess/formats.py`
- Modify: `src/botkin/config.py`, `config.json` (+`.heif`)
- Modify: `src/botkin/api/routes/upload.py`
- Modify: `src/botkin/bot/handlers/upload.py`
- Test: `tests/test_formats.py`

- [ ] **Step 1: Написать падающий тест формат-сниффа**

Создать `tests/test_formats.py`:

```python
from botkin.preprocess.formats import resolve_extension, sniff_extension

ALLOWED = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def test_sniff_known_formats():
    assert sniff_extension(b"%PDF-1.7\n...") == ".pdf"
    assert sniff_extension(b"\xff\xd8\xff\xe0\x00\x10") == ".jpg"
    assert sniff_extension(b"\x89PNG\r\n\x1a\n\x00\x00") == ".png"
    assert sniff_extension(b"RIFF\x00\x00\x00\x00WEBP") == ".webp"
    assert sniff_extension(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00") == ".heic"
    assert sniff_extension(b"randomgarbagebytes") is None


def test_resolve_prefers_valid_extension():
    assert resolve_extension("IMG.HEIC", b"randomgarbage....", ALLOWED) == ".heic"


def test_resolve_falls_back_to_content():
    # iPhone-файл без расширения, но HEIC по содержимому
    head = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
    assert resolve_extension("file_0", head, ALLOWED) == ".heic"


def test_resolve_none_when_unknown():
    assert resolve_extension("x.bin", b"garbagebytes....", ALLOWED) is None
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_formats.py -q`
Expected: FAIL (ModuleNotFoundError: `botkin.preprocess.formats`).

- [ ] **Step 3: Создать `preprocess/formats.py`**

```python
"""Определение формата загруженного файла по содержимому (magic-байты).

iPhone отправляет HEIC файлом часто без расширения или как .heif — поэтому полагаемся
на содержимое, а имя файла используем лишь как подсказку.
"""
from __future__ import annotations

from pathlib import Path

# Бренды ISO-BMFF (box ftyp) для HEIF/HEIC.
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1", b"heif"}


def sniff_extension(head: bytes) -> str | None:
    """Каноничное расширение по первым байтам файла или None."""
    if head[:4] == b"%PDF":
        return ".pdf"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return ".heic"
    return None


def resolve_extension(filename: str | None, head: bytes, allowed: set[str]) -> str | None:
    """Расширение из имени (если валидно), иначе по содержимому. None — формат не поддержан."""
    ext = Path(filename or "").suffix.lower()
    if ext in allowed:
        return ext
    return sniff_extension(head)
```

- [ ] **Step 4: Добавить `.heif` в config**

В `config.py` в `_DEFAULTS["upload"]["allowed_extensions"]` добавить `".heif"`:

```python
        "allowed_extensions": [".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"],
```

И в `config.json` в `upload.allowed_extensions` так же добавить `".heif"`.

- [ ] **Step 5: Валидация по содержимому в `api/routes/upload.py`**

Добавить импорт:

```python
from botkin.preprocess.formats import resolve_extension
```

Заменить начало хендлера (от `ext = ...` до записи файла) на: читать тело ДО валидации,
определять формат по содержимому, сохранять с корректным расширением:

```python
    body = await file.read()
    if len(body) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {len(body)} bytes")
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = resolve_extension(file.filename, body[:32], UPLOAD_ALLOWED_EXTENSIONS)
    if ext is None:
        raise HTTPException(status_code=415, detail="Unsupported file content")

    yyyy_mm = datetime.now(timezone.utc).strftime("%Y-%m")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest_dir = UPLOAD_SOURCES_DIR / str(user_id) / yyyy_mm
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (file.filename or "doc").replace("/", "_").replace("\\", "_")
    # гарантируем корректное расширение (ниже по конвейеру PDF/изображение различаются по суффиксу)
    if Path(safe_name).suffix.lower() not in UPLOAD_ALLOWED_EXTENSIONS:
        safe_name = f"{safe_name}{ext}"
    dest = dest_dir / f"{ts}-{safe_name}"
    dest.write_bytes(body)
```

(Старую строку `body = await file.read()` ниже — удалить, чтение теперь в начале.)

- [ ] **Step 6: Определять расширение из mime в боте `bot/handlers/upload.py`**

Добавить в начало файла:

```python
from pathlib import Path

_MIME_EXT = {
    "image/heic": ".heic", "image/heif": ".heif", "image/jpeg": ".jpg",
    "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf",
}
```

В `on_document` заменить строку формирования имени на:

```python
    filename = doc.file_name or f"doc_{doc.file_unique_id}"
    if not Path(filename).suffix and doc.mime_type in _MIME_EXT:
        filename += _MIME_EXT[doc.mime_type]
```

- [ ] **Step 7: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_formats.py tests/test_smoke.py::test_config_imports -q`
Expected: PASS. Логика 415/приёма закодирована в `resolve_extension` (покрыта юнит-тестами);
HTTP-обвязка — тонкая (3 строки), модель в тестах не дёргаем.

- [ ] **Step 8: Commit**

```bash
git add src/botkin/preprocess/formats.py src/botkin/config.py config.json \
        src/botkin/api/routes/upload.py src/botkin/bot/handlers/upload.py tests/test_formats.py
git commit -m "feat(upload): приём iPhone-HEIC по содержимому (magic-байты) + .heif; ext из mime в боте"
```

---

## Task 8: Финальная проверка

**Files:** —

- [ ] **Step 1: Полный прогон**

Run: `uv run pytest -q`
Expected: все тесты проходят (модель нигде не запускается).

- [ ] **Step 2: Линт**

Run: `uv run ruff check src tests scripts`
Expected: без ошибок (поправить при необходимости).

- [ ] **Step 3: Импорт приложения (без GPU/сети)**

Run: `uv run python -c "import botkin.api.app, botkin.bot.main, botkin.llm.extract, botkin.preprocess.images; print('ok')"`
Expected: печатает `ok`.

- [ ] **Step 4: Демонстрация на example.jpg (опционально, CPU)**

Run: `uv run python -c "from botkin.preprocess.images import prepare_images; r=prepare_images('example.jpg.jpg', long_side=2200, upscale=True, deskew=True, enhance=True); print('ok', len(r), len(r[0]))"`
Expected: печатает `ok 1 <bytes>` — препроцессинг отрабатывает на реальном фото (чтение моделью проверяет пользователь на GPU).

- [ ] **Step 5: Commit (если ruff что-то поправил)**

```bash
git add -A
git commit -m "chore(botkin): финальный прогон Cycle A"
```

---

## Self-review (заполнено автором плана)

**Покрытие спека:**
- G1 (апскейл маленьких): Task 1 (config), Task 2 (_resize двунаправленный, тесты).
- G2 (контраст/резкость): Task 2 (_enhance) + Task 3 (тест).
- G3 (безопасный дескью): Task 2 (_doc_angle/_deskew) + Task 4 (тесты выпрямления и no-op).
- G4 (не ломать поток): Task 5 (extract), classify не менялся (downscale-only), PDF → N изображений (Task 2 тест).
- G5 (подсказка ввода): Task 6.
- G6 (приём iPhone-HEIC без 415): Task 7 (formats.py + резолв по содержимому, +.heif, ext из mime).

**Плейсхолдеры:** нет — код приведён полностью в каждом шаге.

**Согласованность типов:** `prepare_images(path, long_side, upscale, deskew, enhance)`,
`_resize(img, long_side, upscale)`, `_enhance(arr)→arr`, `_doc_angle(arr)→float|None`,
`_deskew(arr)→arr`, `_process(...)→bytes`, `photo_followup_text(image_long_side)→str`,
`IMAGE_EXTRACT_LONG_SIDE` — используются единообразно в Task 1–6.
