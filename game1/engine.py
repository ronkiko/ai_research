#!/usr/bin/env python3
from __future__ import annotations

import argparse
import curses
import sys

from web.server import main as web_main


def _run_legacy_tui() -> int:
    from tui_legacy import MIN_H, MIN_W, main as tui_main

    try:
        curses.wrapper(tui_main)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(f"\nОшибка инициализации экрана: {exc}")
        print("Запускай из обычного интерактивного терминала (не из пайпа/скрипта).")
        print(f"Минимальный размер терминала: {MIN_W}x{MIN_H}.")
        return 1

    print("\nДвижок остановлен.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--tui", action="store_true")
    parsed, rest = parser.parse_known_args(args)

    if parsed.tui:
        return _run_legacy_tui()
    return web_main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
