#!/usr/bin/env python3
"""game1 engine — загрузчик + TUI-рендер с псевдоокнами.

Движок не содержит игровой логики. Он поднимает curses-интерфейс, раскладывает
псевдоокна, загружает автономные модули и после загрузки выбирает механику
(«садится за стол»), вызывая её канонически через MechanicsHost из modules.base.

Запуск из каталога game1/:  python3 engine.py
"""
from __future__ import annotations

import locale
import os
import sys
import textwrap

# Сделать каталог скрипта импортируемым, чтобы `ui` / `modules` разрешались
# независимо от текущего рабочего каталога.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import curses

from ui.theme import init_colors, A, PAIR_OK, PAIR_FAIL, PAIR_WARN, PAIR_DIM, PAIR_TITLE
from ui.console import ConsoleWindow
from modules.base import (
    Module,
    LoadResult,
    Status,
    MechanicsHost,
    MechanicsInfo,
    AiHost,
    ModelInfo,
    ModelStats,
    TrainModeInfo,
    SelectResult,
)
from modules.core import CoreModule
from modules.game_api import GameApiModule

locale.setlocale(locale.LC_ALL, "")

MIN_H, MIN_W = 24, 80

# Манифест модулей: что загружает движок и в каком порядке. Движок — загрузчик,
# поэтому манифест — единственное, что он знает о составе игры.
MODULES: list[type[Module]] = [CoreModule, GameApiModule]


def _status_attr(status: Status) -> int:
    if status is Status.OK:
        return A(PAIR_OK, bold=True)
    if status is Status.WARN:
        return A(PAIR_WARN, bold=True)
    return A(PAIR_FAIL, bold=True)


def _status_tag(status: Status) -> str:
    return {
        Status.OK: "[ ok  ]",
        Status.WARN: "[warn ]",
        Status.FAIL: "[fail ]",
    }[status]


def load_modules(console: ConsoleWindow, stdscr) -> list[Module]:
    """Загрузить модули из манифеста; вернуть список живых экземпляров."""
    console.append("запуск движка: подключение модулей", A(PAIR_DIM))
    console.render(stdscr)
    stdscr.refresh()

    instances: list[Module] = []
    for cls in MODULES:
        module = cls()
        info = module.info()
        console.append(f"[ ... ] {info.name} v{info.version} — {info.summary}", A(PAIR_DIM))
        console.render(stdscr)
        stdscr.refresh()

        try:
            result = module.load()
        except Exception as exc:  # движок должен пережить сломавшийся модуль
            result = LoadResult(Status.FAIL, f"исключение при загрузке: {exc}", info.version)

        ver = result.version or info.version
        line = f"{_status_tag(result.status)} {info.name} v{ver}: {result.message}"
        console.update_last(line, _status_attr(result.status))
        console.render(stdscr)
        stdscr.refresh()
        instances.append(module)

    console.append("", 0)
    return instances


def choose_mechanics_key(options: list[MechanicsInfo]) -> str | None:
    """Хук выбора механики движком.

    Пока — первая по умолчанию (UI выбора появится позже как отдельное
    псевдоокно). Позже тут будет интерактивный выбор и возврат выбранного ключа.
    """
    if not options:
        return None
    return options[0].key


def choose_mechanics(console: ConsoleWindow, stdscr, modules: list[Module]) -> None:
    """Найти хост механик и канонически выбрать/подключить один стол."""
    host = next((m for m in modules if isinstance(m, MechanicsHost)), None)
    if host is None:
        console.append("хост механик не найден — выбор пропущен", A(PAIR_WARN, bold=True))
        console.render(stdscr)
        stdscr.refresh()
        return

    options = host.list_mechanics()
    console.append("доступные столы-механики:", A(PAIR_DIM))
    for opt in options:
        console.append(f"  • {opt.key} — {opt.title}: {opt.summary}", A(PAIR_DIM))
    console.render(stdscr)
    stdscr.refresh()

    chosen = choose_mechanics_key(options)
    if chosen is None:
        console.append("столов нет — нечего выбирать", A(PAIR_WARN, bold=True))
        console.render(stdscr)
        stdscr.refresh()
        return

    console.append(f"[ ... ] сажусь за стол '{chosen}'", A(PAIR_DIM))
    console.render(stdscr)
    stdscr.refresh()

    try:
        result = host.select_mechanics(chosen)
    except Exception as exc:  # движок не падает на сломанной механике
        result = SelectResult(Status.FAIL, f"исключение при выборе: {exc}")

    line = f"{_status_tag(result.status)} механика: {result.message}"
    console.update_last(line, _status_attr(result.status))
    console.render(stdscr)
    stdscr.refresh()

    # Описание выбранной игры (правила + чему учится) — получаем через движок
    # канонически, из MechanicsHost. Позже это будет отдельное окно листания.
    if result.status is Status.OK:
        chosen_info = host.mechanics_info(chosen)
        if chosen_info is not None:
            _show_description(console, stdscr, chosen_info)


def _show_description(console: ConsoleWindow, stdscr, info: MechanicsInfo) -> None:
    """Напечатать бытовое описание правил и цели обучения выбранной игры."""
    width = max(10, console.inner_w)
    console.append("", 0)
    console.append(f"Правила игры «{info.title}»:", A(PAIR_DIM, bold=True))
    for line in textwrap.wrap(info.rules, width=width):
        console.append(line, A(PAIR_DIM))
    console.append("", 0)
    console.append("Чему тут учится модель:", A(PAIR_DIM, bold=True))
    for line in textwrap.wrap(info.learns, width=width):
        console.append(line, A(PAIR_DIM))
    console.render(stdscr)
    stdscr.refresh()


def manage_ai(console: ConsoleWindow, stdscr, modules: list[Module]) -> None:
    """Найти адаптер ИИ (AiHost) и показать модели + статистику; выбрать одну."""
    ai = next((m for m in modules if isinstance(m, AiHost)), None)
    if ai is None:
        console.append("адаптер ИИ не найден — модели пропущены", A(PAIR_WARN, bold=True))
        console.render(stdscr)
        stdscr.refresh()
        return

    options = ai.list_models()
    console.append("доступные модели:", A(PAIR_DIM))
    for opt in options:
        console.append(f"  • {opt.key} — {opt.title} (параметров: {opt.n_params}): {opt.summary}",
                       A(PAIR_DIM))
    console.render(stdscr)
    stdscr.refresh()

    # Пока выбираем первую по умолчанию; позже — окно выбора модели.
    chosen = options[0].key if options else None
    if chosen is None:
        console.append("моделей нет — нечего выбирать", A(PAIR_WARN, bold=True))
        console.render(stdscr)
        stdscr.refresh()
        return

    res = ai.select_model(chosen)
    console.append(f"{_status_tag(res.status)} модель: {res.message}", _status_attr(res.status))
    console.render(stdscr)
    stdscr.refresh()

    # Статистика выбранной модели: нейроны, веса, шаги — получаем через движок.
    stats = ai.model_stats(chosen)
    if stats is not None:
        weights = ", ".join(f"{k}={v:.3f}" for k, v in stats.params.items())
        console.append(
            f"  статистика: нейронов {stats.n_neurons}, параметров {stats.info.n_params}, "
            f"шагов {stats.steps}, веса [{weights}]",
            A(PAIR_DIM),
        )
        console.render(stdscr)
        stdscr.refresh()


def manage_train_mode(console: ConsoleWindow, stdscr, modules: list[Module]) -> None:
    """Показать режимы обучения, выбрать по умолчанию, напечатать справку по нему."""
    ai = next((m for m in modules if isinstance(m, AiHost)), None)
    if ai is None:
        return

    modes = ai.list_train_modes()
    console.append("режимы обучения:", A(PAIR_DIM))
    for m in modes:
        console.append(f"  • {m.key} — {m.title}: {m.summary}", A(PAIR_DIM))
    console.render(stdscr)
    stdscr.refresh()

    # По умолчанию — первый режим (supervised). Позже — окно выбора режима.
    chosen_mode = modes[0].key if modes else None
    if chosen_mode is not None:
        res = ai.set_train_mode(chosen_mode)
        console.append(f"{_status_tag(res.status)} {res.message}", _status_attr(res.status))
        console.render(stdscr)
        stdscr.refresh()

        # Справка по активному режиму — бытовое описание, без математики.
        active = ai.active_train_mode()
        info = next((m for m in modes if m.key == active), None)
        if info is not None:
            console.append("", 0)
            console.append(f"Справка по режиму «{info.title}»:", A(PAIR_DIM, bold=True))
            for line in textwrap.wrap(info.help, width=max(10, console.inner_w)):
                console.append(line, A(PAIR_DIM))
            console.render(stdscr)
            stdscr.refresh()


def main(stdscr) -> None:
    init_colors()
    curses.curs_set(0)

    h, w = stdscr.getmaxyx()
    if h < MIN_H or w < MIN_W:
        msg = f"Терминал маловат: нужно минимум {MIN_W}x{MIN_H}, сейчас {w}x{h}."
        try:
            stdscr.addstr(h // 2, max(0, (w - len(msg)) // 2), msg, A(PAIR_FAIL, bold=True))
            stdscr.refresh()
            stdscr.getch()
        except curses.error:
            pass
        return

    title = "game1 — движок"
    stdscr.addstr(0, max(0, (w - len(title)) // 2), title, A(PAIR_TITLE, bold=True))

    console = ConsoleWindow(y=2, x=2, h=h - 3, w=w - 4)
    modules = load_modules(console, stdscr)
    choose_mechanics(console, stdscr, modules)
    manage_ai(console, stdscr, modules)
    manage_train_mode(console, stdscr, modules)

    console.append("", 0)
    console.append("движок готов. Нажми Q или Esc для выхода.", A(PAIR_OK, bold=True))
    console.render(stdscr)
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            break


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(f"\nОшибка инициализации экрана: {exc}")
        print("Запускай из обычного интерактивного терминала (не из пайпа/скрипта).")
        print(f"Минимальный размер терминала: {MIN_W}x{MIN_H}.")
        sys.exit(1)
    print("\nДвижок остановлен.")