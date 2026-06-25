"""TitlePane — стартовый экран игры (панель F1) и живой прогон в одном окне.

Без анимации: прогон нужен ради статистики (как растёт точность, ползут веса,
копятся шаги/фишки), псевдографика мира — декорация, лишь плодившая сложную
раскладку. Поэтому окно одно: шапка установок (``Ball • Bias (Supervised)``),
два столбца (статистика прогона + литературный «Мир») и внизу полоска меню с
курсором. Меню — вместо отдельной кнопки и строки-подсказки: курсор движется
по полоске (фон-полоска одним цветом, слот под курсором — другим), Старт в
центре (курсор на нём по умолчанию), слева Модель и Режим, справа Скорость и
Закрыть. ``←/→`` двигают курсор, ``Enter`` активирует подсвеченный пункт.
Скорость циклит темп ``1× → 10× → ∞`` (∞ = без ограничений тиков); Модель и Режим
циклически переключают активную модель/режим обучения (само переключение и лог
в Консоль делает контроллер через канонический ``_apply_change``). Прогон
гоняется прямо тут по тикам контроллера (observe → act → step → train).
Отдельной панели прогона и сцены больше нет — логика проще.
"""
from __future__ import annotations

import curses
from collections import deque
from collections.abc import Callable

import os
import time

from .window import PseudoWindow
from .theme import (
    A, PAIR_DIM, PAIR_OK, PAIR_TITLE, PAIR_BORDER,
    PAIR_BAR, PAIR_BAR_SEL,
)
from modules.base import MechanicsHost, AiHost, Status, ChangeLog
from modules.game_api.modes import mode_info, PLAY


class TitlePane(PseudoWindow):
    # --- фиксированный размер и раскладка (без динамических расчётов) ---
    # Окно 76×21 — точная usable-область над навбаром и под заголовком при
    # минимальном терминале 80×24 (пол движка в engine.py). Контроллер строит
    # панель этого размера по центру области панелей; внутри всё рисуется по
    # фиксированным координатам-константам.
    CANVAS_W = 76
    CANVAS_H = 21

    # Внутренние координаты — относительно левого-верхнего угла внутренней
    # области (отступ +1 от рамки). inner = 74×19 (строки 0..18).
    _HEADER_ROW = 0          # «Ball • Bias (Supervised)»
    _COL_TOP = 2             # строка заголовков столбцов (на 1 строку выше)
    _COL_CONTENT_TOP = 4     # первая строка контента (после подчёркивания)
    _COL_CONTENT_ROWS = 12   # строки 4..15 — контент столбцов
    _STAT_X = 4              # столбец «Статистика» (с отступом от рамки)
    _STAT_W = 26
    _WORLD_X = 31            # = _STAT_X + _STAT_W + 1
    _WORLD_W = 43            # до правого края (74 - 31 = 43)
    _MENU_ROW = 16           # полоска меню с курсором (вместо кнопки + подсказки)

    # Слоты меню на одной строке: (ключ, x, ширина). Старт — по центру (курсор на
    # нём по умолчанию), слева Модель/Режим, справа Save/Скорость. Подписи
    # короткие, чтобы 5 кнопок влезли в 74 колонки. Остальное на строке — фон
    # полоски (PAIR_BAR); слот под курсором перекрашивается (PAIR_BAR_SEL).
    _MENU_ITEMS = (
        ("model", 11, 10),    # 11..20
        ("mode", 23, 8),      # 23..30
        ("start", 33, 10),    # 33..42  Старт
        ("speed", 45, 8),     # 45..52
        ("save",  55, 8),     # 55..62
    )
    _MENU_START_IDX = 2      # курсор по умолчанию — на «Старт»

    # Темп прогона: задержка getch в мс.
    _SPEEDS = ((3000, "0.1×"), (300, "1×"), (30, "10×"), (0, "∞"))

    LABEL_W = 9  # ширина поля подписи «фишки»/«точность» для выравнивания значений

    def __init__(self, host: MechanicsHost | None, ai: AiHost | None,
                 y: int, x: int, h: int, w: int,
                 sink: ChangeLog | None = None,
                 speed_idx: int = 0):
        super().__init__("Старт", y, x, h, w,
                         border_pair=PAIR_BORDER, title_pair=PAIR_TITLE)
        self._host = host
        self._ai = ai
        self._sink = sink
        self._reward = 0       # сумма фишек
        self._hits: deque[int] = deque()  # 1/0 по последним ходам (окно WINDOW)
        self._steps = 0
        self._running = False
        self._cursor = self._MENU_START_IDX  # курсор меню (на «Старт»)
        self._speed_idx = max(0, min(speed_idx, len(self._SPEEDS) - 1))
        self._submenu_open = False
        self._submenu_cursor = 0
        self._submenu_items: list[tuple[str, str, bool]] = []  # (key, label, active)
        self._submenu_callback: Callable[[str], str] = lambda k: "move"
        self._submenu_kind: str = ""  # game/model/mode/speed — для позиционирования

    # --- состояние прогона ---

    WINDOW = 1000  # окно подсчёта точности (последние N ходов)

    def is_running(self) -> bool:
        return self._running

    def start_run(self) -> None:
        """Новый прогон: сбросить активную модель, обнулить статистику и пустить тики."""
        if self._ai is not None:
            mi = self._ai.active_model_info()
            if mi is not None:
                self._ai.reset_model(mi.key)
                if self._sink is not None:
                    self._sink.log_change(
                        "сброс", mi.key, Status.OK,
                        f"модель {mi.key.capitalize()} сброшена к начальным весам",
                    )
        self._reward = 0
        self._hits.clear()
        self._steps = 0
        self._running = True

    def stop_run(self) -> None:
        self._running = False

    # --- меню (полоска с курсором) ---

    def tick_delay(self) -> int:
        """Задержка getch для текущего темпа (0 = без ограничений тиков)."""
        return self._SPEEDS[self._speed_idx][0]

    def speed_label(self) -> str:
        return self._SPEEDS[self._speed_idx][1]

    @property
    def speed_index(self) -> int:
        return self._speed_idx

    _SUBMENU_SLOTS = frozenset({"start", "model", "mode", "speed"})

    def _slot_to_submenu(self, slot_key: str) -> str:
        return "game" if slot_key == "start" else slot_key

    def handle(self, key: int) -> str | None:
        """Обработать клавишу меню. Возвращает действие для контроллера:
        ``move``/``start``/``stop``/``close``/None.
        """
        # субменю: ↑/↓ — навигация (на границе упираемся), Enter — выбор, Esc — закрыть
        if self._submenu_open:
            if key in (curses.KEY_UP, ord("k")):
                if self._submenu_cursor > 0:
                    self._submenu_cursor -= 1
                return "move"
            if key in (curses.KEY_DOWN, ord("j")):
                if self._submenu_cursor < len(self._submenu_items) - 1:
                    self._submenu_cursor += 1
                return "move"
            if key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
                return self._select_submenu()
            if key == 27:  # Esc
                self._close_submenu()
                return "move"
            return None

        if key in (curses.KEY_LEFT, ord("h")):
            self._cursor = (self._cursor - 1) % len(self._MENU_ITEMS)
            return "move"
        if key in (curses.KEY_RIGHT, ord("l")):
            self._cursor = (self._cursor + 1) % len(self._MENU_ITEMS)
            return "move"
        if key in (curses.KEY_UP, ord("k")):
            slot_key = self._MENU_ITEMS[self._cursor][0]
            if slot_key in self._SUBMENU_SLOTS:
                self._open_submenu(self._slot_to_submenu(slot_key))
                return "move"
            return None
        if key in (ord("\n"), ord("\r"), curses.KEY_ENTER):
            slot_key = self._MENU_ITEMS[self._cursor][0]
            if slot_key in self._SUBMENU_SLOTS - {"start"}:
                self._open_submenu(self._slot_to_submenu(slot_key))
                return "move"
            return self._activate()
        if key == 27:  # Esc — то же, что «Закрыть»
            return "close"
        return None

    def _activate(self) -> str:
        key = self._MENU_ITEMS[self._cursor][0]
        if key == "start":
            if self._running:
                self.stop_run()
                return "stop"
            self.start_run()
            return "start"
        if key == "save":
            self._save_snapshot()
            return "lab"
        return None

    # --- сохранение снапшота весов ---

    def _save_snapshot(self) -> None:
        if self._ai is None or self._host is None:
            return
        mi = self._ai.active_model_info()
        gi = self._host.active_mechanics()
        mode = self._ai.active_train_mode()
        if mi is None or gi is None or mode is None:
            return
        st = self._ai.model_stats(mi.key)
        if st is None:
            return

        model_key = mi.key
        game_key = gi.key
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{game_key}_{mode}.md"

        save_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "research", "weights", model_key,
        )
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)

        acc = self.accuracy()
        lines = [
            f"## Snapshot — {mi.title} на {gi.title}",
            "",
            f"- **Модель:** {mi.key} ({mi.title})",
            f"- **Игра:** {gi.key} ({gi.title})",
            f"- **Режим:** {mode}",
            f"- **Шаги:** {st.steps}",
            f"- **Точность:** {acc}%",
            f"- **logit:** {st.logit:+.4f}",
            f"- **prob:** {st.prob:.4f}",
            f"- **параметров:** {st.info.n_params}",
            f"- **нейронов:** {st.n_neurons}",
            "",
            "### Веса",
            "",
        ]
        for k, v in st.params.items():
            lines.append(f"  `{k}` = {v}")
        lines.append("")

        with open(filepath, "w") as f:
            f.write("\n".join(lines))

        if self._sink is not None:
            self._sink.log_change(
                "сохранение", model_key, Status.OK,
                f"снапшот → {filepath}",
            )

    # --- субменю (игры, модель, режим, скорость) ---

    def _open_submenu(self, kind: str) -> None:
        if kind == "game":
            items, cb = self._submenu_game_items()
        elif kind == "model":
            items, cb = self._submenu_model_items()
        elif kind == "mode":
            items, cb = self._submenu_mode_items()
        elif kind == "speed":
            items, cb = self._submenu_speed_items()
        else:
            return
        self._submenu_items = items
        self._submenu_callback = cb
        self._submenu_cursor = 0
        self._submenu_kind = kind
        self._submenu_open = True

    def _close_submenu(self) -> None:
        self._submenu_open = False
        self._submenu_items.clear()
        self._submenu_kind = ""

    def _select_submenu(self) -> str:
        if not self._submenu_items:
            self._close_submenu()
            return "move"
        key = self._submenu_items[self._submenu_cursor][0]
        result = self._submenu_callback(key)
        self._close_submenu()
        return result

    def _submenu_game_items(self) -> tuple[list[tuple[str, str, bool]], Callable[[str], str]]:
        items: list[tuple[str, str, bool]] = []
        if self._host is not None:
            active = self._host.active_mechanics()
            active_key = active.key if active is not None else None
            for m in self._host.list_mechanics():
                items.append((m.key, m.key.capitalize(), m.key == active_key))

        def on_select(key: str) -> str:
            if self._host is None:
                return "move"
            res = self._host.select_mechanics(key)
            if self._sink is not None:
                self._sink.log_change("игра", key, res.status, res.message)
            return "move"
        return items, on_select

    def _submenu_model_items(self) -> tuple[list[tuple[str, str, bool]], Callable[[str], str]]:
        items: list[tuple[str, str, bool]] = []
        if self._ai is not None:
            active = self._ai.active_model_info()
            active_key = active.key if active is not None else None
            for m in self._ai.list_models():
                items.append((m.key, m.key.capitalize(), m.key == active_key))

        def on_select(key: str) -> str:
            if self._ai is None:
                return "move"
            res = self._ai.select_model(key)
            if self._sink is not None:
                self._sink.log_change("модель", key, res.status, res.message)
            return "move"
        return items, on_select

    def _submenu_mode_items(self) -> tuple[list[tuple[str, str, bool]], Callable[[str], str]]:
        from modules.game_api.modes import mode_info
        items: list[tuple[str, str, bool]] = []
        if self._ai is not None:
            active_key = self._ai.active_train_mode()
            for m in self._ai.list_train_modes():
                info = mode_info(m.key)
                title = info.title.split(" — ")[0] if info is not None else m.key
                items.append((m.key, title, m.key == active_key))

        def on_select(key: str) -> str:
            if self._ai is None:
                return "move"
            res = self._ai.set_train_mode(key)
            if self._sink is not None:
                self._sink.log_change("режим", key, res.status, res.message)
            return "move"
        return items, on_select

    def _submenu_speed_items(self) -> tuple[list[tuple[str, str, bool]], Callable[[str], str]]:
        items: list[tuple[str, str, bool]] = []
        for i, (delay, label) in enumerate(self._SPEEDS):
            items.append((str(i), label, i == self._speed_idx))

        def on_select(key: str) -> str:
            self._speed_idx = int(key)
            if self._sink is not None:
                label = self._SPEEDS[self._speed_idx][1]
                self._sink.log_change("темп", key, Status.OK, f"темп: {label}")
            return "move"
        return items, on_select

    def _menu_label(self, key: str) -> str:
        if key == "start":
            return "■  Stop" if self._running else "▶  Start"
        if key == "speed":
            return "Speed"
        if key == "save":
            return "Save"
        if key == "model":
            return "Model"
        if key == "mode":
            return "Mode"
        return ""

    @staticmethod
    def _mode_tag(mode: str) -> str:
        # Короткая английская подпись режима для меню (полная — в шапке).
        return {"supervised": "sup", "rl": "rl", "play": "play"}.get(mode, mode)

    def accuracy(self) -> int:
        if not self._hits:
            return 0
        return round(100 * sum(self._hits) / len(self._hits))

    def summary(self) -> str:
        return f"точность {self.accuracy()}%, шаги {self._steps}"

    def tick(self) -> None:
        """Один ход: observe → act → step → (train если не Play). Обновить статистику."""
        if self._host is None or self._ai is None:
            return
        obs = self._host.active_observe()
        if obs is None:
            return
        action = self._ai.act(obs)
        outcome = self._host.active_step(action)
        if outcome is None:
            return
        self._ai.train(obs, outcome)
        self._reward += outcome.reward
        self._hits.append(1 if outcome.reward > 0 else 0)
        if len(self._hits) > self.WINDOW:
            self._hits.popleft()
        self._steps += 1

    # --- рендер ---

    def render(self, stdscr) -> None:
        if self.h < 3 or self.w < 8:
            return
        border = curses.color_pair(self.border_pair)
        try:
            stdscr.addstr(self.y, self.x, "┌" + "─" * (self.w - 2) + "┐", border)
            stdscr.addstr(self.y + self.h - 1, self.x,
                          "└" + "─" * (self.w - 2) + "┘", border)
            for r in range(1, self.h - 1):
                stdscr.addstr(self.y + r, self.x, "│", border)
                stdscr.addstr(self.y + r, self.x + self.w - 1, "│", border)
            stdscr.addstr(self.y, self.x + 2, f" {self.title} ",
                          curses.color_pair(self.title_pair) | curses.A_BOLD)
        except curses.error:
            return
        blank = " " * self.inner_w
        for i in range(self.inner_h):
            try:
                stdscr.addstr(self.y + 1 + i, self.x + 1, blank,
                              curses.color_pair(PAIR_DIM))
            except curses.error:
                break

        top, left = self.y + 1, self.x + 1
        IW = self.inner_w

        # --- шапка установок (фиксированная строка 0) ---
        header = self._header_line()
        if header:
            hx = left + max(0, (IW - len(header)) // 2)
            try:
                stdscr.addstr(top + self._HEADER_ROW, hx, header[:IW],
                              A(PAIR_DIM))
            except curses.error:
                pass

        # --- два столбца: статистика + «Мир» (фиксированные координаты) ---
        stat = self._stat_lines()
        world = self._world_lines()
        self._draw_col(stdscr, left + self._STAT_X, top + self._COL_TOP,
                       self._STAT_W, "Статистика", stat,
                       A(PAIR_OK, bold=True), self._COL_CONTENT_ROWS)
        self._draw_col(stdscr, left + self._WORLD_X, top + self._COL_TOP,
                       self._WORLD_W, "Мир", world, A(PAIR_DIM),
                       self._COL_CONTENT_ROWS)

        # --- полоска меню + статусы + субменю (логическая единица) ---
        self._render_menu_bar(stdscr, top, left, IW)

    # --- полоска меню: имена слотов, статусы, субменю ---

    _ACTIVE_LABEL_Y = 17  # на 1 строку ниже MENU_ROW=16

    def _render_menu_bar(self, stdscr, top: int, left: int, iw: int) -> None:
        """Нарисовать полоску меню (row 16), статусы под ней (row 17) и субменю."""
        row = top + self._MENU_ROW
        try:
            stdscr.addstr(row, left, " " * iw, A(PAIR_BAR))
        except curses.error:
            pass
        for idx, (mkey, mx, mw) in enumerate(self._MENU_ITEMS):
            selected = idx == self._cursor
            pair = PAIR_BAR_SEL if selected else PAIR_BAR
            label = self._menu_label(mkey)
            cell = label.center(mw)[:mw]
            try:
                stdscr.addstr(row, left + mx, cell, A(pair, bold=selected))
            except curses.error:
                pass

        # статусы под слотами
        for mkey, mx, mw in self._MENU_ITEMS:
            label = self._active_label(mkey)
            if label is None:
                continue
            sx = left + mx + (mw - len(label)) // 2
            sy = top + self._ACTIVE_LABEL_Y
            try:
                stdscr.addstr(sy, sx, label, A(PAIR_OK))
            except curses.error:
                pass

        # субменю (поверх всего)
        if self._submenu_open:
            self._render_submenu(stdscr, top, left)

    def _active_label(self, mkey: str) -> str | None:
        if mkey == "start":
            if self._host is None:
                return None
            active = self._host.active_mechanics()
            return active.key.capitalize() if active is not None else None
        if mkey == "model":
            if self._ai is None:
                return None
            mi = self._ai.active_model_info()
            return mi.key.capitalize() if mi is not None else None
        if mkey == "mode":
            if self._ai is None:
                return None
            m = self._ai.active_train_mode()
            return self._mode_tag(m) if m is not None else None
        if mkey == "speed":
            return self._SPEEDS[self._speed_idx][1]
        if mkey == "save":
            return None
        return None

    # --- столбцы ---

    def _draw_col(self, stdscr, x, top, width, heading, lines, attr,
                  max_rows: int = 64) -> None:
        """Заголовок + подчёркивание + строки столбца."""
        if width <= 0:
            return
        try:
            stdscr.addstr(top, x, heading[:width], A(PAIR_TITLE, bold=True))
            stdscr.addstr(top + 1, x, "─" * min(width, len(heading) + 2),
                          curses.color_pair(PAIR_BORDER))
        except curses.error:
            pass
        for i, ln in enumerate(list(lines)[:max_rows]):
            try:
                stdscr.addstr(top + 2 + i, x, ln[:width], attr)
            except curses.error:
                pass

    def _kv(self, key: str, value: str) -> str:
        return f"{key.ljust(self.LABEL_W)}{value}"

    def _header_line(self) -> str:
        # «Ball • Bias (Supervised)»; в Play — «(без обучения)».
        gi = self._host.active_mechanics() if self._host is not None else None
        mi = self._ai.active_model_info() if self._ai is not None else None
        mode = self._ai.active_train_mode() if self._ai is not None else None
        seg: list[str] = []
        if gi is not None:
            seg.append(gi.key.capitalize())
        if mi is not None:
            seg.append(mi.key.capitalize())
        head = " • ".join(seg)
        if mode is not None:
            if mode == PLAY:
                mval = "без обучения"
            else:
                info = mode_info(mode)
                mval = info.title.split(" — ")[0] if info is not None else mode
            head = f"{head} ({mval})" if head else mval
        return head

    def _stat_lines(self) -> list[str]:
        lines = [
            self._kv("фишки", f"{self._reward:+d}"),
            self._kv("точность", f"{self.accuracy()}%"),
            self._kv("шаги", f"{self._steps}"),
        ]
        mi = self._ai.active_model_info() if self._ai is not None else None
        if mi is not None:
            st = self._ai.model_stats(mi.key)
            if st is not None:
                lines.append(f"logit = {st.logit:+.3f}")
                lines.append(f"p     = {st.prob:.4f}")
                lines.append("веса:")
                for k, v in st.params.items():
                    lines.append(f"  {k} = {v:+.3f}")
        return lines

    def _world_lines(self) -> list[str]:
        lore = self._host.active_world_lore() if self._host is not None else []
        if not lore:
            return ["—"]
        # Группируем по абзацам (пустая строка — разделитель) и переносим каждый
        # абзац по словам под фиксированную ширину литературного столбца.
        out: list[str] = []
        para: list[str] = []

        def flush():
            if para:
                out.extend(self._wrap(" ".join(para), self._WORLD_W))
                out.append("")

        for line in lore:
            if line == "":
                flush()
                para = []
            else:
                para.append(line)
        flush()
        if out and out[-1] == "":
            out.pop()
        return out

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        if width <= 0:
            return [text]
        lines, cur = [], ""
        for w in text.split():
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= width:
                cur = f"{cur} {w}"
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    # --- субменю (игры, модель, режим, скорость) ---

    _SUBMENU_Y = 12       # после контента столбцов, над полоской меню
    # x-координаты субменю под соответствующим слотом
    _SUBMENU_X = {
        "game": 33,      # под Старт
        "model": 11,     # под Model
        "mode": 23,      # под Mode
        "speed": 45,     # под Speed
    }
    _SUBMENU_W = 18       # ширина подложки

    def _render_submenu(self, stdscr, top: int, left: int) -> None:
        """Нарисовать субменю: подложка PAIR_BAR, текст с паддингом."""
        sx = left + self._SUBMENU_X.get(self._submenu_kind, 33)
        sy = top + self._SUBMENU_Y
        sw = self._SUBMENU_W
        for i, (key, title, active) in enumerate(self._submenu_items):
            y = sy + i
            selected = i == self._submenu_cursor
            pair = PAIR_BAR_SEL if selected else PAIR_BAR
            marker = "• " if active else "  "
            label = f" {marker}{title}  "[:sw]
            # подложка во всю ширину
            try:
                stdscr.addstr(y, sx, " " * sw, A(pair))
            except curses.error:
                pass
            try:
                stdscr.addstr(y, sx, label, A(pair, bold=selected))
            except curses.error:
                pass