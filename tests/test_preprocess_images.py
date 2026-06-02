import io

import pymupdf
import pytest
from PIL import Image

from botkin.preprocess.images import prepare_images, to_base64_jpegs


def _make_pdf(tmp_path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i} Гемоглобин 145 г/л")
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _make_png(tmp_path, size=(4000, 3000)):
    img = Image.new("RGB", size, (255, 255, 255))
    path = tmp_path / "photo.png"
    img.save(str(path))
    return path


def test_pdf_yields_one_image_per_page(tmp_path):
    path = _make_pdf(tmp_path, pages=2)
    images = prepare_images(path)
    assert len(images) == 2
    for raw in images:
        Image.open(io.BytesIO(raw))   # валидный JPEG, не бросает


def test_large_photo_downscaled(tmp_path):
    path = _make_png(tmp_path, size=(4000, 3000))
    images = prepare_images(path)
    assert len(images) == 1
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) <= 1800   # IMAGE_MAX_LONG_SIDE по умолчанию


def test_classify_variant_smaller(tmp_path):
    path = _make_png(tmp_path, size=(4000, 3000))
    images = prepare_images(path, long_side=1000)
    img = Image.open(io.BytesIO(images[0]))
    assert max(img.size) <= 1000


def test_to_base64(tmp_path):
    path = _make_png(tmp_path, size=(800, 600))
    b64 = to_base64_jpegs(prepare_images(path))
    assert isinstance(b64, list) and b64 and isinstance(b64[0], str)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare_images(tmp_path / "nope.pdf")
