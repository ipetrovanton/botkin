"""CLI-утилиты (пока заглушка)."""
import argparse
import sys
from .db.connection import init_db


def main() -> int:
    parser = argparse.ArgumentParser(prog="botkin")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("init-db", help="Инициализировать базу данных")

    args = parser.parse_args()
    if args.cmd == "init-db":
        init_db()
        print("✅ База данных инициализирована.")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())