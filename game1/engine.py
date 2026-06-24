#!/usr/bin/env python3
"""game1 engine — загрузчик + TUI-рендер с псевдоокнами и нижним навбаром.

Движок не содержит игровой логики. Он поднимает curses-интерфейс, рисует
нижний навбар (F1 Help / F2 Models / F3 Games / F4 Modes), загружает автономные
модули и после загрузки даёт оператору интерактивно выбрать стол, модель и
режим обучения — через псевдоокна со списком и справкой по курсору.

Запуск из каталога game1/:  python3 engine.py
"""
from __future__ import annotations

import locale
import os
import sys

# Сделать каталог скрипта импортируемым, чтобы `ui` / `modules` разрешались
# независимо от текущего рабочего каталога.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import curses

from ui.theme import init_colors, A, PAIR_OK, PAIR_FAIL, PAIR_WARN, PAIR_DIM, PAIR_TITLE
from ui.console import ConsoleWindow
from ui.navbar import NavBar
from ui.listpane import ListPane, ListItem
from ui.help_pane import HelpPane
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

# Пункты навбара: (клавиша, подпись, pane_id). pane_id=None — не панель (Q — выход).
NAVBAR_ITEMS = [
    ("F1", "Help", "help"),
    ("F2", "Models", "models"),
    ("F3", "Games", "games"),
    ("F4", "Modes", "modes"),
    ("Q", "Выход", None),
]


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
    console.append("движок готов. Выбери игру/модель/режим в навбаре (F1–F4).",
                   A(PAIR_OK, bold=True))
    return instances


# ---------------------------------------------------------------------------
# Построение панелей выбора (список + справка по курсору)
# ---------------------------------------------------------------------------

def _build_games_pane(host: MechanicsHost, y, x, h, w) -> ListPane:
    infos = host.list_mechanics()
    active = host.active_mechanics()
    active_key = active.key if active is not None else None
    items = [ListItem(it.key, it.title, active=(it.key == active_key)) for it in infos]

    def detail_for(item: ListItem) -> tuple[str, str]:
        info = host.mechanics_info(item.key)
        if info is None:
            return item.title, ""
        body = f"Правила:\n{info.rules}\n\nЧему тут учится модель:\n{info.learns}"
        if item.active:
            body = "(активная игра)\n\n" + body
        return f"Игра «{info.title}»", body

    def on_select(item: ListItem) -> str:
        try:
            res = host.select_mechanics(item.key)
        except Exception as exc:
            return f"ошибка: {exc}"
        # Пересчитать пометки активного пункта.
        for it in items:
            it.active = (it.key == item.key)
        return f"{_status_tag(res.status)} {res.message}"

    return ListPane("F3 Games — выбор мини-игры", y, x, h, w,
                    items, detail_for, on_select)


def _build_models_pane(ai: AiHost, y, x, h, w) -> ListPane:
    infos = ai.list_models()
    active = ai.active_model_info()
    active_key = active.key if active is not None else None
    items = [ListItem(it.key, it.title, active=(it.key == active_key)) for it in infos]

    def detail_for(item: ListItem) -> tuple[str, str]:
        info = ai.model_info(item.key)
        stats = ai.model_stats(item.key)
        if info is None:
            return item.title, ""
        lines = [info.summary]
        if stats is not None:
            weights = ", ".join(f"{k}={v:.3f}" for k, v in stats.params.items())
            lines.append(f"параметров: {stats.info.n_params}, нейронов: {stats.n_neurons}")
            lines.append(f"шагов обучения: {stats.steps}")
            lines.append(f"веса: {weights}")
        mode = ai.active_train_mode()
        if item.active and mode is not None:
            lines.append(f"активный режим обучения: {mode}")
        return f"Модель «{info.title}»", "\n".join(lines)

    def on_select(item: ListItem) -> str:
        try:
            res = ai.select_model(item.key)
        except Exception as exc:
            return f"ошибка: {exc}"
        for it in items:
            it.active = (it.key == item.key)
        return f"{_status_tag(res.status)} {res.message}"

    return ListPane("F2 Models — выбор модели", y, x, h, w,
                    items, detail_for, on_select)


def _build_modes_pane(ai: AiHost, y, x, h, w) -> ListPane:
    modes = ai.list_train_modes()
    active = ai.active_train_mode()
    items = [ListItem(m.key, m.title, active=(m.key == active)) for m in modes]

    def detail_for(item: ListItem) -> tuple[str, str]:
        info = next((m for m in modes if m.key == item.key), None)
        if info is None:
            return item.title, ""
        return f"Справка по режиму «{info.title}»", info.help

    def on_select(item: ListItem) -> str:
        try:
            res = ai.set_train_mode(item.key)
        except Exception as exc:
            return f"ошибка: {exc}"
        for it in items:
            it.active = (it.key == item.key)
        return f"{_status_tag(res.status)} {res.message}"

    return ListPane("F4 Modes — режим обучения", y, x, h, w,
                    items, detail_for, on_select)


# ---------------------------------------------------------------------------
# Контроллер панелей + навбар
# ---------------------------------------------------------------------------

def run_ui(stdscr, modules: list[Module], console: ConsoleWindow) -> None:
    """Построить панели и крутить главный цикл навигации до выхода."""
    h, w = stdscr.getmaxyx()
    # Главная область — над навбаром, под заголовком.
    pane_y, pane_h = 2, h - 3   # навбар на строке h-1, отступ 1 от него
    pane_x, pane_w = 2, w - 4

    host = next((m for m in modules if isinstance(m, MechanicsHost)), None)
    ai = next((m for m in modules if isinstance(m, AiHost)), None)

    # Тихо выставить умолчания, чтобы состояние было валидным; панели сами
    # покажут активный пункт и позволят его сменить.
    if host is not None:
        opts = host.list_mechanics()
        if opts:
            host.select_mechanics(opts[0].key)
    if ai is not None:
        opts = ai.list_models()
        if opts:
            ai.select_model(opts[0].key)
        modes = ai.list_train_modes()
        if modes:
            ai.set_train_mode(modes[0].key)

    help_pane = HelpPane(y=pane_y, x=pane_x, h=pane_h, w=pane_w)
    games_pane = _build_games_pane(host, pane_y, pane_x, pane_h, pane_w) if host else None
    models_pane = _build_models_pane(ai, pane_y, pane_x, pane_h, pane_w) if ai else None
    modes_pane = _build_modes_pane(ai, pane_y, pane_x, pane_h, pane_w) if ai else None

    panes = {
        "console": console,
        "help": help_pane,
        "games": games_pane,
        "models": models_pane,
        "modes": modes_pane,
    }
    active = "console"
    navbar = NavBar(NAVBAR_ITEMS)

    def render() -> None:
        pane = panes.get(active)
        if pane is not None:
            pane.render(stdscr)
        navbar.render(stdscr, active)
        stdscr.refresh()

    render()
    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        # F1–F4: открыть панель или вернуться в Консоль, если уже открыта.
        fmap = {curses.KEY_F1: "help", curses.KEY_F2: "models",
                curses.KEY_F3: "games", curses.KEY_F4: "modes"}
        if key in fmap:
            target = fmap[key]
            if panes.get(target) is None:
                continue  # такой панели нет (нет хоста)
            active = "console" if active == target else target
            render()
            continue
        if key == 27:  # Esc: из панели — в Консоль; из Консоли — выход
            if active == "console":
                break
            active = "console"
            render()
            continue
        # Стрелки/Enter делегируем в активную панель (если она их понимает).
        pane = panes.get(active)
        if pane is not None and hasattr(pane, "handle"):
            if pane.handle(key):
                render()
                continue
        # Прочие клавиши игнорируем.


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

    # Консоль временно строится для лога загрузки; потом run_ui создаст свою.
    h, w = stdscr.getmaxyx()
    console = ConsoleWindow(y=2, x=2, h=h - 3, w=w - 4)
    modules = load_modules(console, stdscr)
    run_ui(stdscr, modules, console)


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