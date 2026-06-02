"""Парсинг периода: пресеты и ручной ввод дат."""
import calendar
from datetime import datetime


def preset_range(preset: str, now: datetime) -> tuple[datetime, datetime]:
    if preset == "month":
        start = now.replace(month=now.month - 1) if now.month > 1 else now.replace(year=now.year - 1, month=12)
    elif preset == "3m":
        m = now.month - 3
        start = now.replace(year=now.year + (m - 1) // 12, month=(m - 1) % 12 + 1)
    elif preset == "year":
        start = now.replace(year=now.year - 1)
    else:  # all
        start = datetime(1970, 1, 1)
    return start, now


def _end_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_manual(args: list[str]) -> tuple[datetime, datetime] | None:
    """Принимает ['YYYY-MM','YYYY-MM'] или ['YYYY-MM-DD','YYYY-MM-DD']."""
    if len(args) != 2:
        return None
    try:
        a, b = args
        if len(a) == 7:  # YYYY-MM
            sy, sm = map(int, a.split("-"))
            ey, em = map(int, b.split("-"))
            start = datetime(sy, sm, 1, 0, 0, 0)
            end = datetime(ey, em, _end_of_month(ey, em), 23, 59, 59)
        else:            # YYYY-MM-DD
            start = datetime.strptime(a, "%Y-%m-%d").replace(hour=0, minute=0, second=0)
            end = datetime.strptime(b, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        return start, end
    except (ValueError, TypeError):
        return None
