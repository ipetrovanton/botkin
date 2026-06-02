"""Inline-клавиатуры и компактное кодирование callback_data (лимит 64 байта)."""

_SEP = ":"

# Коды типов для краткого callback_data.
TYPE_CODES = {"a": "analysis", "p": "prescription", "d": "doctor_report", "all": None}
CODE_BY_TYPE = {"analysis": "a", "prescription": "p", "doctor_report": "d", None: "all"}


def encode_cb(action: str, *parts) -> str:
    return _SEP.join([action, *[str(p) for p in parts]])


def decode_cb(data: str) -> tuple[str, list[str]]:
    action, *parts = data.split(_SEP)
    return action, parts
