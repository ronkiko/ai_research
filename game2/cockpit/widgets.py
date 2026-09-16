"""Small reusable controls; all hit testing obeys the panel clip boundary."""

import pygame as pg

BG = (12, 18, 27)
CARD = (21, 30, 43)
FIELD = (30, 42, 57)
LINE = (49, 65, 83)
TEXT = (231, 239, 247)
MUTED = (144, 163, 184)
ACCENT = (84, 216, 180)
RED = (255, 135, 139)


class Canvas:
    def __init__(self, surface):
        self.surface = surface
        self.fonts = {n: pg.font.SysFont("dejavusans", n) for n in (13, 15, 18, 24, 30)}

    def text(self, value, x, y, size=15, color=TEXT, width=None):
        value = str(value)
        font = self.fonts[size]
        if width is not None:
            while value and font.size(value)[0] > width:
                value = value[:-2] + "…" if len(value) > 2 else ""
        self.surface.blit(font.render(value, True, color), (x, y))

    def card(self, rect):
        pg.draw.rect(self.surface, CARD, rect, border_radius=14)
        pg.draw.rect(self.surface, LINE, rect, 1, border_radius=14)

    def wrap(self, value, rect, color=MUTED, size=13):
        font = self.fonts[size]
        y = rect.y
        line = ""
        words = []
        for word in str(value).split():
            while font.size(word)[0] > rect.w and len(word) > 1:
                count = len(word)
                while count > 1 and font.size(word[:count])[0] > rect.w:
                    count -= 1
                words.append(word[:count])
                word = word[count:]
            words.append(word)
        for word in words:
            if font.size(line + " " + word)[0] > rect.w and line:
                self.text(line, rect.x, y, size, color, width=rect.w)
                y += font.get_linesize() + 3
                line = ""
            line = (line + " " + word).strip()
        if line:
            self.text(line, rect.x, y, size, color, width=rect.w)
        return y + font.get_linesize()


class Controls:
    def __init__(self):
        self.items = {}
        self.focus = None
        self.select_all = False
        self.menu = None

    def begin(self):
        self.items = {}

    def button(self, c, key, label, rect, callback, enabled=True, primary=False):
        pg.draw.rect(
            c.surface, ACCENT if primary and enabled else FIELD, rect, border_radius=7
        )
        c.text(
            label,
            rect.x + 10,
            rect.y + 9,
            13,
            BG if primary and enabled else TEXT if enabled else MUTED,
            width=rect.w - 20,
        )
        clip = c.surface.get_clip().clip(rect)
        self.items[key] = (clip, callback, enabled, None)

    def input(self, c, key, value, rect, callback):
        pg.draw.rect(c.surface, FIELD, rect, border_radius=7)
        pg.draw.rect(
            c.surface, ACCENT if self.focus == key else LINE, rect, 1, border_radius=7
        )
        display = value or "По умолчанию"
        if self.focus == key and len(display) > 25:
            display = display[-25:]
        c.text(display, rect.x + 10, rect.y + 9, 13, width=rect.w - 20)
        self.items[key] = (c.surface.get_clip().clip(rect), callback, True, value)

    def select(self, c, key, value, rect, options, callback):
        def show():
            self.menu = (key, rect.copy(), list(options), callback)

        self.button(c, key, str(value) + "  ▾", rect, show, enabled=len(options) > 1)

    def overlay(self, c):
        if self.menu is None:
            return
        key, anchor, options, callback = self.menu
        height = 36 * len(options)
        y = min(anchor.bottom + 4, c.surface.get_height() - height - 12)
        rect = pg.Rect(anchor.x, y, anchor.w, height)
        pg.draw.rect(c.surface, FIELD, rect, border_radius=7)
        pg.draw.rect(c.surface, ACCENT, rect, 1, border_radius=7)
        self.menu_rects = []
        for i, value in enumerate(options):
            row = pg.Rect(rect.x, rect.y + i * 36, rect.w, 36)
            c.text(value, row.x + 10, row.y + 9, 13, width=row.w - 20)
            self.menu_rects.append((row, value))

    def event(self, event):
        if self.menu is not None:
            if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                self.menu = None
                return True
            if event.type == pg.MOUSEBUTTONDOWN:
                callback = self.menu[3]
                for rect, value in getattr(self, "menu_rects", []):
                    if rect.collidepoint(event.pos):
                        callback(value)
                        break
                self.menu = None
                return True
            if event.type in (pg.KEYDOWN, pg.MOUSEWHEEL):
                return True
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            self.focus = None
            for key, (rect, callback, enabled, value) in self.items.items():
                if enabled and rect.collidepoint(event.pos):
                    if value is None:
                        callback()
                    else:
                        self.focus = key
                        self.select_all = True
                    return True
        if event.type == pg.KEYDOWN and self.focus in self.items:
            _, callback, _, value = self.items[self.focus]
            if event.key in (pg.K_RETURN, pg.K_ESCAPE, pg.K_TAB):
                self.focus = None
            elif event.key == pg.K_a and event.mod & pg.KMOD_CTRL:
                self.select_all = True
            elif event.key == pg.K_BACKSPACE:
                value = "" if self.select_all else value[:-1]
                callback(value)
                self.select_all = False
                self.items[self.focus] = (
                    self.items[self.focus][0],
                    callback,
                    True,
                    value,
                )
            elif event.unicode and event.unicode.isprintable():
                value = ("" if self.select_all else value) + event.unicode
                callback(value)
                self.select_all = False
                self.items[self.focus] = (
                    self.items[self.focus][0],
                    callback,
                    True,
                    value,
                )
            return True
        return False
