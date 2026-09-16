"""Monitor, experiment form, and results own their drawing boundaries."""

import pygame as pg
from level import load_level
from physics import Body
from tile_renderer import TileRenderer
from session import POLICIES, ALGORITHMS
from .widgets import *


class MonitorPanel:
    def __init__(self):
        self.path = None
        self.renderer = None
        self.cache = None
        self.key = None

    def draw(self, c, rect, p):
        c.card(rect)
        path = p.active.level if p.active else p.draft.level
        c.text("ЭКРАН МИРА", rect.x + 18, rect.y + 14, 13, ACCENT)
        c.text(path.stem, rect.right - 190, rect.y + 14, 13, MUTED, width=170)
        area = rect.inflate(-28, -126)
        area.y = rect.y + 72
        active = p.active
        context = (
            (
                "Игрок"
                if active.controller == "Human"
                else active.bot_mode + " / " + active.execution
            )
            if active
            else "Предпросмотр"
        )
        c.text(
            f"{context}  ·  эпизод {p.statistics.episode}  ·  тик {p.statistics.tick}",
            rect.x + 18,
            rect.y + 38,
            13,
            MUTED,
            width=rect.w - 36,
        )
        if active and active.controller == "Bot" and active.bot_mode == "Training" and active.execution == "Auto":
            c.text("Fast Auto Training", area.x, area.y + 42, 24, ACCENT, width=area.w)
            c.text(
                "External runtime\nWorld preview disabled for benchmark",
                area.x,
                area.y + 92,
                15,
                MUTED,
                width=area.w,
            )
            return
        if path != self.path:
            self.level = load_level(path)
            self.renderer = TileRenderer(pg, self.level)
            self.surface = pg.Surface((self.level.width, self.level.height))
            self.path = path
            self.key = None
        body = Body(**p.snapshot["body"]) if p.snapshot else self.level.new_body()
        key = (repr(body), area.size)
        if key != self.key:
            self.renderer.present(self.surface, body)
            # Keep the hazard visible; deep underground rows need not fill the screen.
            height = self.level.height
            scale = min(area.w / self.level.width, area.h / height)
            self.cache = pg.transform.scale(
                self.surface,
                (max(1, int(self.level.width * scale)), max(1, int(height * scale))),
            )
            self.key = key
        c.surface.blit(self.cache, self.cache.get_rect(center=area.center))
        label = (
            "→  Разбег    ↑  Прыжок    R  Сначала"
            if p.active_human
            else "Физика 120 Гц  ·  независима от частоты интерфейса"
        )
        c.text(label, rect.x + 18, rect.bottom - 36, 13, MUTED, width=rect.w - 36)


class SetupPanel:
    def __init__(self):
        self.scroll = 0
        self.content_height = 0
        self.viewport = pg.Rect(0, 0, 0, 0)

    def draw(self, c, rect, footer, p, controls):
        c.card(rect)
        c.text("ЭКСПЕРИМЕНТ", rect.x + 18, rect.y + 16, 13, ACCENT)
        self.viewport = pg.Rect(
            rect.x + 16, rect.y + 46, rect.w - 32, footer.y - rect.y - 58
        )
        self.scroll = min(self.scroll, max(0, self.content_height - self.viewport.h))
        old = c.surface.get_clip()
        c.surface.set_clip(self.viewport)
        x = self.viewport.x
        w = self.viewport.w
        y = self.viewport.y - self.scroll
        for i, (key, label) in enumerate(
            (("human", "Играть"), ("train", "Обучать"), ("play", "Проверять"))
        ):
            controls.button(
                c,
                "mode_" + key,
                label,
                pg.Rect(x + i * (w // 3), y, w // 3 - 4, 36),
                lambda key=key: p.set_mode(key),
                primary=p.mode == key,
            )
        y += 52

        def choice(key, label, value, options, setter):
            nonlocal y
            c.text(label, x, y, 13, MUTED)
            y += 24
            controls.select(c, key, value, pg.Rect(x, y, w, 36), list(options), setter)
            y += 48

        def number(key, label):
            nonlocal y
            c.text(label, x, y, 13, MUTED)
            y += 24
            controls.input(
                c,
                key,
                p.numbers[key],
                pg.Rect(x, y, w, 36),
                lambda v: p.numbers.__setitem__(key, v),
            )
            y += 48

        choice(
            "level",
            "Карта",
            p.draft.level.stem,
            p.levels,
            lambda v: setattr(p.draft, "level", p.levels[v]),
        )
        if p.mode == "human":
            y = (
                c.wrap(
                    "Разбегитесь перед прыжком. В воздухе горизонтальная скорость сохраняется. Перепрыгните яму и достигните финиша.",
                    pg.Rect(x, y, w, 120),
                )
                + 20
            )
        else:
            c.text(f"MLP {p.draft.network}  /  {p.draft.algorithm}", x, y, 15)
            y += 30
            if p.mode == "train":
                choice(
                    "execution",
                    "Исполнение",
                    p.draft.execution,
                    ("Realtime", "Auto"),
                    lambda v: setattr(p.draft, "execution", v),
                )
            number("episodes", "Количество эпизодов")
            if p.mode == "train":
                choice(
                    "checkpoint_mode",
                    "Начальные веса",
                    p.draft.checkpoint_mode,
                    ("Resume", "Fresh"),
                    lambda v: setattr(p.draft, "checkpoint_mode", v),
                )
            if p.mode == "train":
                y = (
                    c.wrap(
                        "Resume — продолжить; Fresh — начать с нуля с резервной копией прежних весов.",
                        pg.Rect(x, y, w, 60),
                    )
                    + 14
                )
            path, exists = p.checkpoint_label()
            y = (
                c.wrap(
                    ("Веса найдены: " if exists else "Веса ещё не созданы: ")
                    + path.name,
                    pg.Rect(x, y, w, 50),
                    ACCENT if exists else MUTED,
                )
                + 14
            )
            if p.mode == "play":
                y = (
                    c.wrap(
                        "Проверка использует сохранённые веса. Обновление модели выключено.",
                        pg.Rect(x, y, w, 60),
                    )
                    + 14
                )
            controls.button(
                c,
                "advanced",
                "Дополнительно  " + ("−" if p.advanced else "+"),
                pg.Rect(x, y, w, 36),
                lambda: setattr(p, "advanced", not p.advanced),
            )
            y += 48
            if p.advanced:
                choice(
                    "network",
                    "Сеть",
                    p.draft.network,
                    POLICIES,
                    lambda v: setattr(p.draft, "network", v),
                )
                choice(
                    "algorithm",
                    "Алгоритм",
                    p.draft.algorithm,
                    ALGORITHMS,
                    lambda v: setattr(p.draft, "algorithm", v),
                )
                if p.mode == "train" and p.draft.execution == "Auto":
                    number("auto_speed", "Лимит скорости симуляции, ×")
                number("seed", "Seed модели")
                c.text("Файл весов (.pt), пусто — стандартный", x, y, 13, MUTED)
                y += 24
                controls.input(
                    c,
                    "checkpoint_file",
                    p.draft.checkpoint_file,
                    pg.Rect(x, y, w, 36),
                    lambda v: setattr(p.draft, "checkpoint_file", v),
                )
                y += 48
                s = p.statistics
                y = (
                    c.wrap(
                        f"Обновлений модели в серии: {s.updated_episodes}; loss: {s.last_loss:.3f}. Всего успехов {s.success_rate_total:.1%}; средний терминальный тик {s.mean_terminal_tick_100:.0f}. Диагностика: late {s.late} · rejected {s.rejected} · прыжки {s.jump_applied}/{s.jump_requested}",
                        pg.Rect(x, y, w, 60),
                    )
                    + 16
                )
        self.content_height = y + self.scroll - self.viewport.y
        c.surface.set_clip(old)
        if self.content_height > self.viewport.h:
            bar = pg.Rect(
                rect.right - 9,
                self.viewport.y
                + int(self.scroll / self.content_height * self.viewport.h),
                3,
                max(20, int(self.viewport.h**2 / self.content_height)),
            )
            pg.draw.rect(c.surface, MUTED, bar, border_radius=2)
        pg.draw.line(
            c.surface, LINE, (footer.x, footer.y - 8), (footer.right, footer.y - 8)
        )
        active = p.controller.status in ("Running", "Starting", "Finishing")
        label = (
            "Применить и перезапустить"
            if active and p.dirty
            else {
                "human": "Начать игру",
                "train": "Начать обучение",
                "play": "Проверить модель",
            }[p.mode]
        )
        if p.busy:
            label = "Подождите…"
        controls.button(
            c,
            "start",
            label,
            pg.Rect(footer.x, footer.y, footer.w, 40),
            p.start,
            enabled=not p.busy and not (active and not p.dirty),
            primary=True,
        )
        controls.button(
            c,
            "stop",
            "Остановить",
            pg.Rect(footer.x, footer.y + 48, footer.w, 36),
            p.stop,
            enabled=not p.busy and (active or p.controller.game is not None),
        )

    def event(self, event):
        if event.type == pg.MOUSEWHEEL and self.viewport.collidepoint(
            pg.mouse.get_pos()
        ):
            self.scroll = max(
                0,
                min(
                    max(0, self.content_height - self.viewport.h),
                    self.scroll - event.y * 42,
                ),
            )
            return True
        return False


class ResultsPanel:
    def draw(self, c, rect, p, controls):
        c.card(rect)
        s = p.statistics
        c.text("РЕЗУЛЬТАТЫ СЕРИИ", rect.x + 18, rect.y + 14, 13, ACCENT)
        controls.button(
            c,
            "export",
            "Сохранено" if p.export_path else "Экспорт",
            pg.Rect(rect.right - 124, rect.y + 8, 108, 34),
            lambda: self.export(p),
            enabled=s.attempts > 0,
        )
        attempts = (
            f"{s.attempts} / {p.active.episodes}"
            if p.active and p.active.controller == "Bot"
            else str(s.attempts)
        )
        values = [
            ("Попытки", attempts),
            ("Успех / всего", f"{s.successes} / {s.attempts}"),
            (
                "Последние 100",
                f"{s.success_rate_100:.0%}" if s.episodes_window else "—",
            ),
        ]
        width = (rect.w - 36) // 3
        for i, (label, value) in enumerate(values):
            x = rect.x + 18 + i * width
            c.text(label, x, rect.y + 55, 13, MUTED, width=width - 8)
            c.text(value, x, rect.y + 78, 24, width=width - 8)
        result = {
            "success": "Финиш",
            "die": "Гибель",
            "timeout": "Лимит времени",
            "timeout_or_reset": "Лимит времени / сброс",
        }.get(s.last_result, s.last_result or "Нет завершённых попыток")
        c.text(
            f"{result}  ·  {p.elapsed:.0f} с  ·  {s.speed:.1f}×",
            rect.x + 18,
            rect.y + 117,
            13,
            MUTED,
            width=rect.w - 36,
        )
        history = list(p.history)
        total = max(1, len(history))
        barwidth = min(12, (rect.w - 36) / total)
        for i, event in enumerate(history):
            pg.draw.rect(
                c.surface,
                ACCENT if event.get("result") == "success" else RED,
                pg.Rect(
                    rect.x + 18 + i * barwidth,
                    rect.bottom - 32,
                    max(1, barwidth - 2),
                    10,
                ),
                border_radius=2,
            )
        if not history:
            c.text(
                "Здесь появится история последних 100 попыток",
                rect.x + 18,
                rect.bottom - 34,
                13,
                MUTED,
                width=rect.w - 36,
            )

    @staticmethod
    def export(p):
        try:
            p.export_results()
        except OSError as error:
            p.message(f"Не удалось сохранить отчёт: {error}", error=True)
