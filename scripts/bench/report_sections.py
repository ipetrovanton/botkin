from __future__ import annotations

import argparse
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    text = args.path.read_text(encoding="utf-8", errors="replace")
    sections = re.split(r"(?=^#{2,3} .+$)", text, flags=re.MULTILINE)
    patterns = ("garmin", "погода", "лекар", "медикамент", "физичес", "нагруз", "образ жизни", "итог", "рекоменд")
    for section in sections:
        if any(pattern in section.lower() for pattern in patterns):
            print("\n" + section[:1800].strip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
