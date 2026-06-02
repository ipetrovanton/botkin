from botkin.bot.keyboards import TYPE_CODES, decode_cb, encode_cb


def test_roundtrip_doc():
    assert decode_cb(encode_cb("doc", 22)) == ("doc", ["22"])


def test_roundtrip_list():
    cb = encode_cb("lst", "a", 7)
    assert decode_cb(cb) == ("lst", ["a", "7"])


def test_roundtrip_nav():
    assert decode_cb(encode_cb("nav", 5, "next")) == ("nav", ["5", "next"])


def test_under_64_bytes():
    cb = encode_cb("per", "month", "labs")
    assert len(cb.encode("utf-8")) <= 64


def test_type_codes_map_all():
    assert set(TYPE_CODES.values()) >= {"analysis", "prescription", "doctor_report"}
