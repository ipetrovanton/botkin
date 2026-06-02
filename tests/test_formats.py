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
