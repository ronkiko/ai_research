#!/usr/bin/env python3
"""game1 legacy TUI engine — загрузчик + curses-рендер с псевдоокнами.

Движок не содержит игровой логики. Он поднимает curses-интерфейс, рисует
нижний навбар (F1 Старт / F2 Models / F3 Games / F4 Modes), загружает автономные
модули и после загрузки даёт оператору интерактивно выбрать стол, модель и
режим обучения — через псевдоокна со списком и справкой по курсору. F1 открывает
единое окно игры (установки + статистика + «Мир» + полоска меню с курсором):
←/→ двигают курсор по меню (Пауза / Скорость / Старт / Закрыть), Enter активирует
подсвеченный пункт. «Старт» пускает живой прогон активного стола с активной
моделью прямо в этом окне (цикл observe → act → step → train по тикам),
статистика обновляется на месте; «Скорость» переключает темп вплоть до режима
без ограничений тиков.

Временный legacy-запуск из каталога game1/:  python3 engine.py --tui
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
from ui.treepane import TreePane, TreeNode
from ui.labpane import LabPane
from ui.title_pane import TitlePane
from app.module_loader import load_application_modules
from modules.base import (
    Module,
    Status,
    MechanicsHost,
    AiHost,
    SelectResult,
    ChangeLog,
)
from config import load_config, save_config, default_settings, Settings

import json
import time

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_RESULTS_PATH = os.path.join(_ENGINE_DIR, "examples", "results", "validate.json")
_RESULTS_TTL = 60
_RESULTS_CACHE: dict = {"ts": 0.0, "data": None}

_MODELS_MD_DIR = os.path.join(_ENGINE_DIR, "modules", "game_api", "models")
_MD_CACHE: dict[str, str] = {}


def _model_md(key: str) -> str:
    """Содержимое .md файла модели, или сообщение об отсутствии."""
    if key not in _MD_CACHE:
        path = os.path.join(_MODELS_MD_DIR, f"{key}.md")
        try:
            with open(path) as f:
                _MD_CACHE[key] = f.read().strip()
        except FileNotFoundError:
            _MD_CACHE[key] = f"(нет справки: {key}.md)"
    return _MD_CACHE[key]

_MODEL_LABEL = {"bias": "Bias", "logistic": "Logistic", "duplet": "Duplet", "context": "Context", "mlp": "MLP", "torch": "Следователь"}
_MODE_LABEL = {"supervised": "Supervised", "rl": "RL", "rl+adaptive": "RL+Adapt", "play": "Play"}


def _top_for_game(game_key: str, n: int = 3) -> list[dict]:
    now = time.time()
    if now - _RESULTS_CACHE["ts"] >= _RESULTS_TTL:
        _RESULTS_CACHE["data"] = None
    if _RESULTS_CACHE["data"] is None:
        try:
            with open(_RESULTS_PATH) as f:
                _RESULTS_CACHE["data"] = json.load(f)
            _RESULTS_CACHE["ts"] = now
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    return _RESULTS_CACHE["data"].get("top_per_game", {}).get(game_key, [])[:n]

locale.setlocale(locale.LC_ALL, "")

MIN_H, MIN_W = 24, 80

# Пункты навбара: (клавиша, подпись, pane_id). pane_id=None — не панель (Q — выход).
NAVBAR_ITEMS = [
    ("F1", "Старт", "title"),
    ("F2", "Лаба", "lab"),
    ("F3", "Games", "games"),
    ("F4", "Models", "models"),
    ("F5", "Modes", "modes"),
    ("Q", "Выход", None),
]

# Группы уровней для древовидного браузера столов (F3). Порядок здесь = порядок
# в дереве. Каждая группа — папка в modules/core/mechanics/levelN/.
LEVEL_FOLDERS = [
    ("level0", "Уровень 0 — линейно разделимые"),
    ("level1", "Уровень 1 — один скрытый слой"),
    ("level2", "Уровень 2 — память / последовательности"),
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

    loaded = load_application_modules()
    for module, result in zip(loaded.modules, loaded.results):
        info = module.info()
        console.append(f"[ ... ] {info.name} v{info.version} — {info.summary}", A(PAIR_DIM))
        console.render(stdscr)
        stdscr.refresh()

        ver = result.version or info.version
        line = f"{_status_tag(result.status)} {info.name} v{ver}: {result.message}"
        console.update_last(line, _status_attr(result.status))
        console.render(stdscr)
        stdscr.refresh()

    console.append("", 0)
    return loaded.modules


# ---------------------------------------------------------------------------
# Журнал изменений настроек: единый путь вывода в Консоль
# ---------------------------------------------------------------------------

class ConsoleChangeLog(ChangeLog):
    """ChangeLog поверх ConsoleWindow: каждое изменение настройки — строка статуса."""

    def __init__(self, console: ConsoleWindow):
        self._console = console

    def log_change(self, scope: str, key: str, status: Status, message: str) -> None:
        # scope/key — метаданные изменения (что и по какому ключу); в Консоль
        # выводим самопонятное сообщение хоста со статусом, без префикса scope,
        # чтобы не было «модель: активна модель …» / «режим: режим …».
        line = f"{_status_tag(status)} {message}"
        self._console.append(line, _status_attr(status))


def _apply_change(sink: ChangeLog, scope: str, key: str, fn) -> SelectResult:
    """Выполнить настройку fn(key) и залогировать результат через sink.

    fn — канонический метод хоста (select_mechanics / select_model /
    set_train_mode), возвращающий SelectResult. Все изменения настроек идут
    через эту функцию, поэтому ни одно не проходит мимо Консоли.
    """
    try:
        res = fn(key)
    except Exception as exc:  # движок не падает на сломанной настройке
        res = SelectResult(Status.FAIL, f"исключение: {exc}")
    sink.log_change(scope, key, res.status, res.message)
    return res


# ---------------------------------------------------------------------------
# Построение панелей выбора (список + справка по курсору)
# ---------------------------------------------------------------------------

def _build_games_pane(host: MechanicsHost, sink: ChangeLog, y, x, h, w) -> TreePane:
    infos = host.list_mechanics()
    active = host.active_mechanics()
    active_key = active.key if active is not None else None

    # Собрать дерево: группы по уровням, внутри — столы из механик.
    # Раскрыта только группа, в которой находится активная игра; остальные
    # свёрнуты, чтобы сразу было видно, где сейчас оператор.
    active_group: str | None = None
    if active_key in ("ball", "dealer"):
        active_group = "level0"
    elif active_key in ("kormushka", "lie_detector", "drift"):
        active_group = "level1"
    elif active_key == "pattern":
        active_group = "level2"

    groups: dict[str, TreeNode] = {
        key: TreeNode(key, title, children=[],
                      expanded=(key == active_group), info_key=key)
        for key, title in LEVEL_FOLDERS
    }
    for it in infos:
        if it.key in ("ball", "dealer"):
            target = "level0"
        elif it.key in ("kormushka", "lie_detector", "drift"):
            target = "level1"
        elif it.key == "pattern":
            target = "level2"
        else:
            target = "level0"
        node = TreeNode(it.key, it.title, active=(it.key == active_key),
                        english=it.key.capitalize())
        groups[target].children.append(node)
    root = TreeNode("root", "Столы", children=list(groups.values()),
                    expanded=True)

    def detail_for(item: ListItem) -> tuple[str, str]:
        info = host.mechanics_info(item.key)
        if info is None:
            return item.title, ""
        lines = [f"Правила:\n{info.rules}\n\nЧему тут учится модель:\n{info.learns}"]
        if item.active:
            lines.insert(0, "(активная игра)")
        top = _top_for_game(info.key)
        if top:
            lines.append("")
            lines.append("Топ-3 по результатам валидации:")
            for i, r in enumerate(top, 1):
                mt = _MODE_LABEL.get(r["mode"], r["mode"])
                ml = _MODEL_LABEL.get(r["model"], r["model"])
                lines.append(f"  {i}. {ml} + {mt} — {r['acc']:.0%}")
        body = "\n".join(lines)
        return f"Игра {item.english} «{info.title}»", body

    def group_help(level_key: str) -> str | None:
        return {
            "level0": (
                "Линейно разделимые задачи. Граница между ответами — прямая,\n"
                "поэтому хватает одного нейрона (bias / logistic)."
            ),
            "level1": (
                "Нелинейно разделимые задачи. Нужно объединить несколько\n"
                "линейных границ — один скрытый слой (MLP / torch)."
            ),
            "level2": (
                "Задачи с памятью / последовательностями. Ответ зависит от\n"
                "прошлых входов, поэтому нужно окно или рекуррентная сеть."
            ),
        }.get(level_key)

    def on_select(node: TreeNode) -> str:
        res = _apply_change(sink, "игра", node.key, host.select_mechanics)
        pane.set_active(node.key)
        return f"{_status_tag(res.status)} {res.message}"

    cursor = 0
    for idx, it in enumerate(root.children):
        if it.is_group and it.expanded:
            for cidx, child in enumerate(it.children):
                if child.active:
                    cursor = idx + cidx + 1
                    break
    pane = TreePane("games", "F3 Games — выбор мини-игры", y, x, h, w,
                    root, detail_for, on_select, group_help=group_help,
                    cursor=cursor)
    return pane


def _build_models_pane(ai: AiHost, sink: ChangeLog, y, x, h, w) -> ListPane:
    infos = ai.list_models()
    active = ai.active_model_info()
    active_key = active.key if active is not None else None
    items = [ListItem(it.key, it.title, active=(it.key == active_key),
                      english=it.key.capitalize()) for it in infos]

    def detail_for(item: ListItem) -> tuple[str, str]:
        info = ai.model_info(item.key)
        stats = ai.model_stats(item.key)
        if info is None:
            return item.title, ""
        md = _model_md(item.key)
        lines = [md]
        if stats is not None:
            lines.append("")
            weights = ", ".join(f"{k}={v:.3f}" for k, v in stats.params.items())
            lines.append(f"параметров: {stats.info.n_params}, нейронов: {stats.n_neurons}")
            lines.append(f"шагов обучения: {stats.steps}")
            lines.append(f"веса: {weights}")
        mode = ai.active_train_mode()
        if item.active and mode is not None:
            lines.append(f"активный режим обучения: {mode}")
        return f"Модель {item.english} \u00ab{info.title}\u00bb", "\n".join(lines)

    def on_select(item: ListItem) -> str:
        res = _apply_change(sink, "модель", item.key, ai.select_model)
        for it in items:
            it.active = (it.key == item.key)
        return f"{_status_tag(res.status)} {res.message}"

    cursor = next((i for i, it in enumerate(items) if it.active), 0)
    return ListPane("models", "F4 Models \u2014 выбор модели", y, x, h, w,
                    items, detail_for, on_select, cursor=cursor)


def _build_modes_pane(ai: AiHost, sink: ChangeLog, y, x, h, w) -> ListPane:
    modes = ai.list_train_modes()
    active = ai.active_train_mode()
    items = [ListItem(m.key, m.title, active=(m.key == active),
                      english=m.title.split(" \u2014 ")[0]) for m in modes]

    def detail_for(item: ListItem) -> tuple[str, str]:
        info = next((m for m in modes if m.key == item.key), None)
        if info is None:
            return item.title, ""
        return f"Справка по режиму \u00ab{info.title}\u00bb", info.help

    def on_select(item: ListItem) -> str:
        res = _apply_change(sink, "режим", item.key, ai.set_train_mode)
        for it in items:
            it.active = (it.key == item.key)
        return f"{_status_tag(res.status)} {res.message}"

    cursor = next((i for i, it in enumerate(items) if it.active), 0)
    return ListPane("modes", "F5 Modes \u2014 режим обучения", y, x, h, w,
                    items, detail_for, on_select, cursor=cursor)


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

    # Журнал изменений настроек — единый путь в Консоль. Через него идут и
    # применение конфига/пресета на старте, и выборы оператора из панелей: ни
    # одно изменение не проходит мимо Консоли.
    sink = ConsoleChangeLog(console)

    # Конфиг настроек: ищем файл при старте. Есть и валиден — грузим; нет или
    # сломан — генерируем пресет по умолчанию (один раз). Всё это логируется.
    settings, cfg_status, cfg_msg = load_config(host, ai)
    sink.log_change("конфиг", "", cfg_status, cfg_msg)
    if settings is None:
        settings = default_settings(host, ai)
        sink.log_change("конфиг", "", Status.OK, "сгенерирован пресет по умолчанию")

    # Применить настройки (из конфига или пресета) — каждое через _apply_change,
    # чтобы попасть в Консоль единым путём.
    if host is not None and settings.game:
        _apply_change(sink, "игра", settings.game, host.select_mechanics)
    if ai is not None:
        if settings.model:
            _apply_change(sink, "модель", settings.model, ai.select_model)
        if settings.train_mode:
            _apply_change(sink, "режим", settings.train_mode, ai.set_train_mode)

    console.append("", 0)
    console.append("движок готов. F1 — старт игры, F2–F4 — выбор модели/игры/режима.",
                   A(PAIR_OK, bold=True))

    # TitlePane — единственная поверхность игры: статистика + «Мир» + кнопка
    # «Старт/Стоп», прогон гоняется прямо в ней. Фиксированного размера 76×21
    # (точная usable-область при минимальном терминале 80×24), по центру области
    # панелей; пол 80×24 гарантирует, что помещается, поэтому внутри раскладка
    # идёт по фиксированным координатам-константам. Отдельной панели прогона нет.
    title_pane = None
    if host and ai:
        tpx = pane_x + max(0, (pane_w - TitlePane.CANVAS_W) // 2)
        tpy = pane_y + max(0, (pane_h - TitlePane.CANVAS_H) // 2)
        title_pane = TitlePane(
            host, ai, pane_y, pane_x, pane_h, pane_w,
            sink=sink,
            speed_idx=settings.speed,
        )
    lab_pane = LabPane(pane_y, pane_x, pane_h, pane_w)
    games_pane = _build_games_pane(host, sink, pane_y, pane_x, pane_h, pane_w) if host else None
    models_pane = _build_models_pane(ai, sink, pane_y, pane_x, pane_h, pane_w) if ai else None
    modes_pane = _build_modes_pane(ai, sink, pane_y, pane_x, pane_h, pane_w) if ai else None

    panes = {
        "console": console,
        "title": title_pane,
        "lab": lab_pane,
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
        # Таймаут getch синхронизируем ДО вызова, чтобы при смене
        # running/темпа он вступал в силу немедленно, а не через итерацию.
        if active == "title" and title_pane is not None:
            running = title_pane.is_running()
            stdscr.timeout(title_pane.tick_delay() if running else -1)

        key = stdscr.getch()

        # --- стартовая панель (F1): меню с курсором + прогон в одном окне ---
        # Меню (Модель / Режим / Старт / Скорость / Закрыть) работает и в покое, и
        # во время прогона: ←/→ двигают курсор, Enter активирует подсвеченный
        # пункт. Пока прогон идёт, getch неблокирующий — каждое срабатывание
        # таймера (возврат -1) это один тик цикла observe → act → step → train.
        if active == "title" and title_pane is not None:
            # Тик таймера — один ход цикла (только когда прогон идёт).
            if key == -1:
                if running:
                    title_pane.tick()
                render()
                continue

            # Q — выход (с логом итога, если прогон шёл).
            if key in (ord("q"), ord("Q")):
                if running:
                    sink.log_change("игра", "", Status.OK,
                                    f"прогон остановлен: {title_pane.summary()}")
                    title_pane.stop_run()
                stdscr.timeout(-1)
                break

            # F1–F5: навигация. Во время прогона F3–F5 игнорируются (фокус на
            # прогоне); F1–F2 (в Консоль/Лабу) и Esc/Закрыть всегда доступны.
            fmap = {curses.KEY_F1: "title", curses.KEY_F2: "lab",
                    curses.KEY_F3: "games", curses.KEY_F4: "models",
                    curses.KEY_F5: "modes"}
            if key in fmap:
                target = fmap[key]
                if target == "lab" and panes.get("lab") is not None:
                    panes["lab"]._refresh()
                if running and target in ("models", "games", "modes"):
                    continue
                if target != "title" and panes.get(target) is None:
                    continue
                if running:
                    sink.log_change("игра", "", Status.OK,
                                    f"прогон остановлен: {title_pane.summary()}")
                    title_pane.stop_run()
                stdscr.timeout(-1)
                active = "console" if active == target else target
                render()
                continue

            # Клавиши меню делегируются в TitlePane.handle.
            action = title_pane.handle(key)
            if action == "start":
                gi = host.active_mechanics() if host is not None else None
                mi = ai.active_model_info() if ai is not None else None
                gname = gi.title if gi is not None else "?"
                mname = mi.key.capitalize() if mi is not None else "?"
                sink.log_change("игра", "", Status.OK,
                                f"запущен прогон: {gname} × {mname}")
            elif action == "stop":
                sink.log_change("игра", "", Status.OK,
                                f"прогон остановлен: {title_pane.summary()}")
            elif action == "speed":
                sink.log_change("игра", "", Status.OK,
                                f"темп: {title_pane.speed_label()}")
            elif action == "model":
                if ai is not None:
                    mi = ai.active_model_info()
                    if mi is not None:
                        sink.log_change("игра", "", Status.OK,
                                        f"активна модель {mi.key.capitalize()} ({mi.title})")
            elif action == "mode":
                if ai is not None:
                    mode = ai.active_train_mode()
                    if mode is not None:
                        sink.log_change("игра", "", Status.OK,
                                        f"режим {mode}")
            elif action == "close":
                if title_pane.is_running():
                    sink.log_change("игра", "", Status.OK,
                                    f"прогон остановлен: {title_pane.summary()}")
                title_pane.stop_run()
                stdscr.timeout(-1)
                active = "console"
            elif action == "lab":
                if panes.get("lab") is not None:
                    panes["lab"]._refresh()
                    active = "lab"
            # move / noop / None — просто перерисовать.
            render()
            continue

        # --- обычная навигация по панелям ---
        if key in (ord("q"), ord("Q")):
            break
        # F1–F5: открыть панель или вернуться в Консоль, если уже открыта.
        fmap = {curses.KEY_F1: "title", curses.KEY_F2: "lab",
                curses.KEY_F3: "games", curses.KEY_F4: "models",
                curses.KEY_F5: "modes"}
        if key in fmap:
            target = fmap[key]
            if target == "lab" and panes.get("lab") is not None:
                panes["lab"]._refresh()  # обновить список снапшотов
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

    # Выход: вернуть getch в блокирующий режим и сохранить изменённые в
    # интерфейсе настройки в конфиг. Текущее состояние берём из хостов (что
    # реально активно), логируем через sink.
    stdscr.timeout(-1)
    active_game = host.active_mechanics() if host is not None else None
    active_model = ai.active_model_info() if ai is not None else None
    active_mode = ai.active_train_mode() if ai is not None else None
    cur = Settings(
        game=active_game.key if active_game is not None else "",
        model=active_model.key if active_model is not None else "",
        train_mode=active_mode or "",
        speed=title_pane.speed_index if title_pane is not None else 0,
    )
    save_status, save_msg = save_config(cur)
    sink.log_change("конфиг", "", save_status, save_msg)
    active = "console"
    render()
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
