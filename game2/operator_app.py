"""Composition root for the native laboratory cockpit (no simulation in the UI)."""

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame as pg
from session import SETTINGS_PATH
from cockpit.presenter import LabPresenter
from cockpit.layout import layout
from cockpit.widgets import Canvas, Controls, BG, TEXT, MUTED, ACCENT, RED, CARD
from cockpit.panels import MonitorPanel, SetupPanel, ResultsPanel


class OperatorApp:
    GUI_HZ = 60

    def __init__(self, *, settings_path=SETTINGS_PATH, pygame_module=None):
        pg.init()
        info = pg.display.Info()
        self.screen = pg.display.set_mode(
            (
                max(960, min(1440, info.current_w - 60)),
                max(640, min(900, info.current_h - 80)),
            ),
            pg.RESIZABLE,
        )
        pg.display.set_caption("Game2 · Лаборатория управления")
        self.presenter = LabPresenter(settings_path)
        self.controls = Controls()
        self.canvas = Canvas(self.screen)
        self.monitor = MonitorPanel()
        self.setup = SetupPanel()
        self.results = ResultsPanel()
        self.right = False
        self.log_open = False
        self.log_scroll = 0
        self.log_height = 0

    def release(self):
        self.right = False
        self.presenter.controller.set_human_action(right=False)

    def draw(self):
        p = self.presenter
        c = self.canvas
        c.surface = self.screen
        self.screen.fill(BG)
        self.controls.begin()
        self.bounds = layout(self.screen.get_size())
        b = self.bounds
        c.text("GAME / LAB", b.header.x, b.header.y, 24, ACCENT)
        c.text(
            "Лаборатория управления · платформер",
            b.header.x,
            b.header.y + 34,
            13,
            MUTED,
        )
        status = {
            "Stopped": "Готово",
            "Running": "Выполняется",
            "Starting": "Запуск",
            "Stopping": "Остановка",
            "Finished": "Завершено",
            "Error": "Ошибка",
            "Finishing": "Сохранение",
        }.get(p.controller.status, p.controller.status)
        c.text(
            status,
            b.header.right - 330,
            b.header.y + 12,
            15,
            RED if p.error else ACCENT,
        )
        self.controls.button(
            c,
            "journal",
            "Журнал",
            pg.Rect(b.header.right - 192, b.header.y + 5, 90, 36),
            lambda: setattr(self, "log_open", not self.log_open),
        )
        self.controls.button(
            c,
            "exit",
            "Выход",
            pg.Rect(b.header.right - 94, b.header.y + 5, 94, 36),
            p.close,
        )
        self.monitor.draw(c, b.monitor, p)
        self.results.draw(c, b.results, p, self.controls)
        self.setup.draw(c, b.setup, b.footer, p, self.controls)
        if p.dirty:
            c.text(
                "Есть неприменённые настройки",
                b.header.x + 420,
                b.header.y + 36,
                13,
                MUTED,
                width=280,
            )
        if p.error or self.log_open:
            self.release()
            self.controls.menu = None
            rect = pg.Rect(
                b.monitor.x + 12, b.monitor.y + 12, b.monitor.w - 24, b.monitor.h - 24
            )
            pg.draw.rect(self.screen, CARD, rect, border_radius=10)
            pg.draw.rect(
                self.screen, RED if p.error else MUTED, rect, 1, border_radius=10
            )
            self.controls.items = {}
            self.log_view = pg.Rect(rect.x + 16, rect.y + 44, rect.w - 32, rect.h - 100)
            c.text(
                "Ошибка" if p.error else "Журнал событий · прокрутка колёсиком",
                rect.x + 16,
                rect.y + 14,
                13,
                RED if p.error else ACCENT,
            )
            old = self.screen.get_clip()
            self.screen.set_clip(self.log_view)
            self.log_scroll = min(
                self.log_scroll, max(0, self.log_height - self.log_view.h)
            )
            y = self.log_view.y - self.log_scroll
            entries = (
                [p.error]
                if p.error
                else [t + "  " + m for t, m, _ in reversed(p.messages)]
            )
            for entry in entries:
                y = (
                    c.wrap(
                        entry,
                        pg.Rect(self.log_view.x, y, self.log_view.w, 100),
                        RED if p.error else TEXT,
                    )
                    + 12
                )
            self.log_height = y + self.log_scroll - self.log_view.y
            self.screen.set_clip(old)
            self.controls.button(
                c,
                "dismiss",
                "Закрыть",
                pg.Rect(rect.right - 112, rect.bottom - 44, 96, 34),
                self.dismiss,
            )
        if self.controls.focus not in self.controls.items:
            self.controls.focus = None
        self.controls.overlay(c)
        pg.display.flip()

    def dismiss(self):
        self.presenter.error = ""
        self.log_open = False
        self.log_scroll = 0

    def handle_event(self, event):
        p = self.presenter
        if event.type == pg.QUIT:
            p.close()
            return
        if event.type == pg.VIDEORESIZE:
            self.screen = pg.display.set_mode(
                (max(960, event.w), max(640, event.h)), pg.RESIZABLE
            )
            self.controls.menu = None
            return
        if event.type == pg.WINDOWFOCUSLOST:
            self.release()
            self.controls.focus = None
            return
        if event.type == pg.KEYUP and event.key == pg.K_RIGHT:
            self.release()
            return
        if event.type == pg.MOUSEBUTTONDOWN:
            self.release()
        if self.controls.event(event):
            return
        if p.error or self.log_open:
            self.release()
            if event.type == pg.MOUSEWHEEL:
                self.log_scroll = max(0, self.log_scroll - event.y * 36)
            if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                self.dismiss()
            return
        if self.setup.event(event):
            return
        if event.type == pg.KEYDOWN and p.active_human and not self.controls.focus:
            if event.key == pg.K_RIGHT:
                self.right = True
            elif event.key == pg.K_r:
                p.controller.restart()
            p.controller.set_human_action(
                right=self.right,
                jump=event.key == pg.K_UP and not getattr(event, "repeat", False),
            )

    def run(self):
        clock = pg.time.Clock()
        try:
            while True:
                self.presenter.poll()
                self.draw()
                for event in pg.event.get():
                    self.handle_event(event)
                if self.presenter.closing and not self.presenter.busy:
                    break
                clock.tick(self.GUI_HZ)
        finally:
            self.release()
            self.presenter.close()
            if self.presenter._worker:
                self.presenter._worker.join(timeout=10)
            pg.quit()


def main():
    OperatorApp().run()


if __name__ == "__main__":
    main()
