"""Canvas-only anatomical presentation of BotProfilerBackend descriptors."""
from __future__ import annotations

import math
import tkinter as tk

BG = "#0b1420"
PANEL = "#111f2e"
LINE = "#294254"
TEXT = "#e3edf4"
MUTED = "#8ba4b5"
ACCENT = "#67e1cf"


class HumanoidSchematic(tk.Canvas):
    """Scalable engineering drawing with data-driven, scrollable callouts."""

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=BG, highlightthickness=0, takefocus=True)
        self.on_select = on_select
        self.components = []
        self.selected = None
        self.hitboxes = {}
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._hover)
        self.bind("<Down>", lambda _: self._step(1))
        self.bind("<Up>", lambda _: self._step(-1))
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-int(e.delta / 120), "units"))
        self.bind("<Button-4>", lambda _: self.yview_scroll(-1, "units"))
        self.bind("<Button-5>", lambda _: self.yview_scroll(1, "units"))

    def show(self, components, selected):
        self.components = components
        self.selected = selected
        self.redraw()

    def _hit(self, event):
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        return next((ref for ref, (a, b, c, d) in self.hitboxes.items()
                     if a <= x <= c and b <= y <= d), None)

    def _click(self, event):
        self.focus_set()
        ref = self._hit(event)
        if ref:
            self.on_select(ref)

    def _hover(self, event):
        self.configure(cursor="hand2" if self._hit(event) else "")

    def _step(self, direction):
        refs = [c["component_ref"] for c in self.components]
        if refs:
            index = refs.index(self.selected) if self.selected in refs else 0
            self.on_select(refs[(index + direction) % len(refs)])
        return "break"

    def redraw(self):
        self.delete("all")
        self.hitboxes.clear()
        width = max(300, self.winfo_width())
        height = max(430, self.winfo_height(), 150 + math.ceil(len(self.components) / 2) * 112)
        self.configure(scrollregion=(0, 0, width, height))
        scale = min(width / 820, (height - 100) / 665)
        cx, top = width * .5, 55

        def xy(x, y):
            return cx + x * scale, top + y * scale

        def line(points, color=LINE, thickness=1, **kw):
            self.create_line(*[v for p in points for v in xy(*p)],
                             fill=color, width=thickness, **kw)

        def poly(points):
            self.create_polygon(*[v for p in points for v in xy(*p)],
                                fill="#132534", outline="#527586", width=1.5)

        def oval(x, y, rx, ry, color=LINE, fill=BG, thickness=1):
            a, b = xy(x-rx, y-ry)
            c, d = xy(x+rx, y+ry)
            self.create_oval(a, b, c, d, outline=color, fill=fill, width=thickness)

        for x in range(20, width, 32):
            for y in range(50, height, 32):
                self.create_oval(x, y, x+1, y+1, fill="#20303d", outline="")
        self.create_text(24, 25, text="01  /  HUMANOID ARCHITECTURE", anchor="w",
                         fill=MUTED, font=("DejaVu Sans", 10, "bold"))
        line([(0, -15), (0, 637)], "#254454", dash=(3, 7))
        for y in range(0, 641, 40):
            line([(-156, y), (-148, y)], "#385565")
        # Head shell, neck, clavicle, thorax and pelvis.
        poly([(-28, 3), (-40, 20), (-40, 59), (-27, 83), (0, 92),
              (27, 83), (40, 59), (40, 20), (28, 3)])
        poly([(-18, 91), (-18, 112), (18, 112), (18, 91)])
        poly([(-20, 112), (-78, 125), (-90, 150), (-65, 237),
              (-47, 282), (47, 282), (65, 237), (90, 150), (78, 125), (20, 112)])
        poly([(-47, 285), (-61, 309), (-42, 344), (0, 359),
              (42, 344), (61, 309), (47, 285)])
        for side in (-1, 1):
            def mirror(points):
                return [(side*x, y) for x, y in points]
            poly(mirror([(87, 136), (106, 153), (130, 249), (109, 256), (79, 169)]))
            poly(mirror([(111, 270), (130, 267), (141, 345), (122, 352)]))
            poly(mirror([(123, 362), (142, 359), (149, 392), (141, 408),
                         (126, 402), (120, 382)]))
            poly(mirror([(10, 355), (42, 349), (56, 442), (48, 469), (24, 469)]))
            poly(mirror([(26, 493), (49, 493), (46, 580), (28, 592), (21, 571)]))
            poly(mirror([(27, 596), (48, 595), (62, 622), (17, 622), (18, 609)]))
            for x, y, r in [(89, 146, 14), (120, 261, 12), (133, 356, 8),
                            (33, 345, 12), (37, 480, 14), (35, 593, 8)]:
                oval(side*x, y, r, r, "#6a8b9a", PANEL)
                oval(side*x, y, 4, 4, "#527586", BG)
            line(mirror([(28, 132), (65, 145), (55, 222), (22, 239)]), "#416071")
            for y in range(157, 234, 17):
                line(mirror([(9, y), (47, y+5), (62, y-3)]), "#385565")
            line(mirror([(4, 263), (33, 267), (42, 305), (15, 328)]))
            line(mirror([(8, 164), (84, 154), (119, 260), (134, 356)]), "#35796f")
            line(mirror([(0, 281), (32, 341), (37, 480), (35, 593)]), "#35796f")
            for x in (128, 133, 138):
                line(mirror([(x, 375), (x+3, 395)]), "#527586")
        # Cerebral hemispheres and segmented spinal cord.
        oval(-14, 40, 17, 25, "#86aebc", "#244554")
        oval(14, 40, 17, 25, "#86aebc", "#244554")
        for side in (-1, 1):
            for y in (24, 37, 50):
                line([(side*6, y), (side*20, y+7), (side*13, y+13)], "#78a6b4", smooth=True)
        line([(0, 64), (0, 293)], ACCENT, 2)
        for y in range(103, 285, 12):
            a, b = xy(-6, y)
            c, d = xy(6, y+7)
            self.create_rectangle(a, b, c, d, fill="#427d7e", outline="#79bbb4")
        line([(-66, 638), (66, 638)], "#527586")
        self.create_text(cx, top + 665*scale, text="MotorGoal + proprioception → reflex → actuators",
                         fill=MUTED, font=("DejaVu Sans", 9))
        anchors = {"brain": (0, 40), "spinal_cord": (0, 190),
                   "right": (-37, 410), "jump": (37, 480),
                   "right_leg": (-37, 480), "left_leg": (37, 480),
                   "right_arm": (-120, 261), "left_arm": (120, 261),
                   "balance": (0, 308)}
        for index, component in enumerate(self.components):
            ref = component["component_ref"]
            selected = ref == self.selected
            color = ACCENT if selected else MUTED
            side = index % 2
            row = index // 2
            card_w = min(246, width * .27)
            x = 22 if side == 0 else width-card_w-22
            y = 83 + row * max(112, min(245, (height-205) / max(1, math.ceil(len(self.components)/2))))
            anchor = component["anatomy_anchor"].removeprefix("motor:")
            ax, ay = anchors.get(anchor, ((-1 if side == 0 else 1)*62, 310))
            px, py = xy(ax, ay)
            edge = x+card_w if side == 0 else x
            elbow = edge+18 if side == 0 else edge-18
            self.create_line(edge, y+36, elbow, y+36, elbow, py, px, py,
                             fill=color, width=2 if selected else 1)
            oval(ax, ay, 7, 7, color, BG, 2)
            self.create_rectangle(x, y, x+card_w, y+76, fill="#17343d" if selected else PANEL,
                                  outline=color if selected else LINE, width=2 if selected else 1)
            label = component["label"]
            if ref.startswith("motor:"):
                label = ref.split(":", 1)[1].upper().replace("_", " ") + " Motor"
            self.create_text(x+12, y+20, text=label, anchor="w", width=card_w-22,
                             fill=TEXT, font=("DejaVu Sans", 10, "bold"))
            implementation = component["implementation"].upper()
            topology_label = component["topology_label"]
            detail = ((implementation if implementation == topology_label else implementation + "  /  " + topology_label)
                      if component["enabled"] else "DISABLED  /  unavailable")
            self.create_text(x+12, y+55, text=detail, anchor="w", width=card_w-22,
                             fill=color, font=("DejaVu Sans", 9))
            self.hitboxes[ref] = (x, y, x+card_w, y+76)


def draw_topology(canvas, topology):
    """Draw declared layers, capping visual nodes without changing layer labels."""
    canvas.delete("all")
    width = max(300, canvas.winfo_width())
    if topology["kind"] == "none":
        canvas.create_text(width/2, 65, text="No model instantiated", fill=MUTED,
                           font=("DejaVu Sans", 12))
        return
    layers = [topology["inputs"], *topology["hidden"], topology["outputs"]]
    previous = []
    for index, count in enumerate(layers):
        x = 30 + index * (width-60) / max(1, len(layers)-1)
        points = [(x, 28 + j * 12 + (8-min(count or 1, 8))*6)
                  for j in range(min(count or 1, 8))]
        for a, b in previous:
            for c, d in points:
                canvas.create_line(a, b, c, d, fill=LINE)
        for a, b in points:
            canvas.create_oval(a-4, b-4, a+4, b+4, fill=PANEL, outline=ACCENT)
        canvas.create_text(x, 139, text=str(count) if count else topology["kind"].upper(),
                           fill=TEXT, font=("DejaVu Sans", 11, "bold"))
        previous = points
