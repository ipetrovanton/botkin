import io

import cv2
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
    doc.save(str(path))
    doc.close()
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
    path = _make_png(tmp_path, (560, 800))    # меньше classify-цели (1000)
    images = prepare_images(path, long_side=1000, upscale=False)
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) == 800               # маленькое НЕ растёт (только вниз)


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


def test_enhance_preserves_size_and_valid_jpeg(tmp_path):
    Image.new("RGB", (1000, 1400), (200, 200, 200)).save(str(tmp_path / "g.png"))
    raw = prepare_images(tmp_path / "g.png", long_side=2200, upscale=True, enhance=True)[0]
    out = Image.open(io.BytesIO(raw))
    assert out.mode == "RGB"
    assert max(out.size) == 2200


def _tilted_document(angle_deg):
    # насыщенный фон (как коричневая ткань) + светлый низконасыщенный «документ» ~70%
    bg = (150, 90, 40)
    canvas = np.full((1000, 800, 3), bg, dtype=np.uint8)
    canvas[150:850, 150:650] = (235, 235, 235)
    matrix = cv2.getRotationMatrix2D((400, 500), angle_deg, 1.0)
    return cv2.warpAffine(canvas, matrix, (800, 1000), borderValue=bg)


def test_doc_angle_detects_tilt():
    angle = _doc_angle(_tilted_document(10))
    assert angle is not None and abs(abs(angle) - 10) <= 3   # ~10° с допуском


def test_doc_angle_noop_on_uniform():
    uniform = np.full((1000, 800, 3), 235, dtype=np.uint8)  # «документ» во весь кадр
    assert _doc_angle(uniform) is None                       # площадь > max_area → no-op
