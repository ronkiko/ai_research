"""Native Management editor. All persistent data crosses BotProfilerBackend."""
from __future__ import annotations

import argparse
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk

from .bot_profiler_backend import BotProfilerBackend
from .bot_schematic import ACCENT, BG, LINE, MUTED, PANEL, TEXT, HumanoidSchematic, draw_topology


class BotProfiler:
    def __init__(self, root, backend=None, bot_id="player1", *, maximize=True):
        self.root = root
        self.backend = backend or BotProfilerBackend()
        self.catalog = self.backend.catalog()
        self.bot_id = None
        self.selected = "cerebral_cortex"
        self.description = None
        self.component = None
        self._loading = False
        self._dirty = False
        self._runtime_job = None
        self.root.title("Bot Profiler · Game2 Research")
        self.root.configure(bg=BG)
        self.root.minsize(1000, 700)
        self.root.geometry("1440x900")
        if maximize:
            self.root.attributes("-fullscreen", True)
        self._style()
        self.name = tk.StringVar()
        self.bot_choice = tk.StringVar()
        self.configuration = tk.StringVar()
        self.precision = tk.StringVar()
        self.seed = tk.StringVar()
        self.enabled = tk.BooleanVar()
        self.level = tk.StringVar(value="1")
        self.status = tk.StringVar(value="Ready")
        self._build()
        for variable in (self.name, self.configuration, self.precision, self.seed, self.enabled):
            variable.trace_add("write", self._mark_dirty)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Control-s>", lambda _: self.save())
        self.root.bind("<F11>", self.fullscreen)
        self.root.bind("<Escape>", lambda _: self.root.attributes("-fullscreen", False))
        self.load_bot(bot_id)
        self._poll_runtime()

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=PANEL, foreground=TEXT, font=("DejaVu Sans", 10))
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("DejaVu Sans", 19, "bold"))
        style.configure("TButton", padding=(14, 9), background="#23394a", borderwidth=0)
        style.map("TButton", background=[("active", "#345465")])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG)
        style.map("Accent.TButton", background=[("active", "#9cefe2"), ("disabled", LINE)])
        style.configure("TEntry", fieldbackground=BG, insertcolor=TEXT, padding=7)
        style.configure("TCombobox", fieldbackground=BG, padding=7, arrowsize=14)
        style.map("TCombobox", fieldbackground=[("readonly", BG)],
                  foreground=[("readonly", TEXT), ("disabled", MUTED)])
        style.configure("TCheckbutton", background=PANEL)
        style.map("TCheckbutton", background=[("active", PANEL)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", LINE)

    def _build(self):
        header = ttk.Frame(self.root, padding=(24, 18))
        header.pack(fill="x")
        ttk.Label(header, text="BOT PROFILER", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="GAME2  /  MANAGEMENT", style="Muted.TLabel").pack(side="left", padx=24)
        ttk.Button(header, text="Close", command=self.close).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="Fullscreen  F11", command=self.fullscreen).pack(side="right")
        bar = ttk.Frame(self.root, padding=(24, 10))
        bar.pack(fill="x", pady=(1, 12))
        ttk.Label(bar, text="Bot profile").pack(side="left", padx=(0, 10))
        self.selector = ttk.Combobox(bar, textvariable=self.bot_choice, state="readonly", width=18)
        self.selector.pack(side="left")
        self.selector.bind("<<ComboboxSelected>>", self._switch_bot)
        ttk.Button(bar, text="+ New bot", command=self.create_dialog).pack(side="left", padx=10)
        ttk.Label(bar, text="Name").pack(side="left")
        self.name_entry = ttk.Entry(bar, textvariable=self.name, width=23)
        self.name_entry.pack(side="left", fill="x", expand=True, padx=(8, 12))
        self.save_button = ttk.Button(bar, text="Save profile", style="Accent.TButton", command=self.save)
        self.save_button.pack(side="right")
        ttk.Button(bar, text="Reload", command=self.reload).pack(side="right", padx=8)
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        drawing = tk.Frame(body, bg=BG)
        drawing.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.schematic = HumanoidSchematic(drawing, self.select_component)
        self.schematic.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(drawing, command=self.schematic.yview)
        scrollbar.pack(side="right", fill="y")
        self.schematic.configure(yscrollcommand=scrollbar.set)
        # Inspector scrolls independently on shorter desktops.
        panel = ttk.Frame(body, width=380)
        panel.grid(row=0, column=1, sticky="ns")
        panel.grid_propagate(False)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        self.inspector_canvas = tk.Canvas(panel, bg=PANEL, highlightthickness=0, width=370)
        self.inspector_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(panel, command=self.inspector_canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.inspector_canvas.configure(yscrollcommand=scroll.set)
        self.inspector = ttk.Frame(self.inspector_canvas, padding=22)
        window = self.inspector_canvas.create_window(0, 0, window=self.inspector, anchor="nw")
        self.inspector.bind("<Configure>", lambda _: self.inspector_canvas.configure(
            scrollregion=self.inspector_canvas.bbox("all")))
        self.inspector_canvas.bind("<Configure>", lambda e: self.inspector_canvas.itemconfigure(window, width=e.width))
        self.root.bind("<MouseWheel>", self._scroll_inspector)
        self.root.bind("<Button-4>", self._scroll_inspector)
        self.root.bind("<Button-5>", self._scroll_inspector)
        self._build_inspector()
        runtime_bar = ttk.Frame(self.root, padding=(24, 8))
        runtime_bar.pack(fill="x", pady=(8, 0))
        ttk.Label(runtime_bar, text="TRAINING SET", style="Muted.TLabel").pack(side="left")
        level = ttk.Entry(runtime_bar, textvariable=self.level, width=4)
        level.pack(side="left", padx=10)
        level.bind("<Return>", lambda _: self.refresh_runtime())
        ttk.Button(runtime_bar, text="Refresh", command=self.refresh_runtime).pack(side="left")
        self.runtime_summary = ttk.Label(runtime_bar, text="", style="Muted.TLabel")
        self.runtime_summary.pack(side="left", padx=20)
        footer = tk.Frame(self.root, bg=BG, padx=24, pady=12)
        footer.pack(fill="x")
        tk.Label(footer, textvariable=self.status, bg=BG, fg=ACCENT,
                 anchor="w", wraplength=740).pack(side="left")
        tk.Label(footer, text="Select a callout · ↑ ↓ components · Ctrl+S save", bg=BG,
                 fg=MUTED).pack(side="right")

    def _scroll_inspector(self, event):
        if str(event.widget).startswith(str(self.inspector_canvas)):
            direction = -1 if event.num == 4 else 1 if event.num == 5 else -int(event.delta / 120)
            self.inspector_canvas.yview_scroll(direction, "units")

    def _label(self, text, *, muted=False, pady=(0, 8)):
        label = ttk.Label(self.inspector, text=text, wraplength=310,
                          style="Muted.TLabel" if muted else "TLabel")
        label.pack(anchor="w", fill="x", pady=pady)
        return label

    def _build_inspector(self):
        self._label("02  /  COMPONENT INSPECTOR", muted=True)
        self.component_title = self._label("")
        self.component_title.configure(font=("DejaVu Sans", 17, "bold"))
        self.availability = self._label("", muted=True)
        self.role_label = self._label("")
        self.implementation_label = self._label("")
        self._label("Configuration", muted=True, pady=(12, 4))
        self.config_box = ttk.Combobox(self.inspector, textvariable=self.configuration, state="readonly")
        self.config_box.pack(fill="x")
        self.config_box.bind("<<ComboboxSelected>>", self._configuration_changed)
        self._label("Precision", muted=True, pady=(12, 4))
        self.precision_box = ttk.Combobox(self.inspector, textvariable=self.precision, state="readonly")
        self.precision_box.pack(fill="x")
        self._label("Initialization seed (blank = unspecified)", muted=True, pady=(12, 4))
        self.seed_entry = ttk.Entry(self.inspector, textvariable=self.seed)
        self.seed_entry.pack(fill="x")
        self.enabled_check = ttk.Checkbutton(self.inspector, text="Component enabled", variable=self.enabled)
        self.enabled_check.pack(anchor="w", pady=(12, 8))
        self.topology_title = self._label("Topology", pady=(12, 0))
        self.topology_canvas = tk.Canvas(self.inspector, bg=BG, height=160, highlightthickness=0)
        self.topology_canvas.pack(fill="x", pady=8)
        self.topology_canvas.bind("<Configure>", lambda _: self._draw_topology())
        self.topology_summary = self._label("", muted=True)
        self._label("Layer counts are declared by the profile; dots are capped at eight per layer.", muted=True)
        ttk.Separator(self.inspector).pack(fill="x", pady=16)
        self._label("03  /  TRAINING STATE", muted=True)
        row = ttk.Frame(self.inspector)
        row.pack(fill="x", pady=(0, 12))
        ttk.Label(row, text="Training Set").pack(side="left")
        self.level_entry = ttk.Entry(row, textvariable=self.level, width=5)
        self.level_entry.pack(side="left", padx=10)
        self.level_entry.bind("<Return>", lambda _: self.refresh_runtime())
        ttk.Button(row, text="Refresh", command=self.refresh_runtime).pack(side="right")
        self.runtime_label = self._label("")
        self.secondary_label = self._label("", muted=True)
        self._label("Profile changes apply to the next Training launch. Existing checkpoints are retained.", muted=True,
                    pady=(16, 8))

    def _mark_dirty(self, *_):
        if not self._loading:
            self._dirty = True
            self.status.set("Unsaved changes · Save profile to validate and apply")
            self.save_button.configure(state="normal")

    def _error(self, exc):
        self.status.set(f"Could not apply: {exc}")
        messagebox.showerror("Bot Profiler", str(exc), parent=self.root)

    def _refresh_choices(self):
        self.selector.configure(values=[b["bot_id"] for b in self.backend.list_bots()])
        self.bot_choice.set(self.bot_id or "")

    def load_bot(self, bot_id):
        # Read everything before replacing the currently displayed profile.
        description = self.backend.describe_bot(bot_id)
        profile = self.backend.get_profile(bot_id)
        self.bot_id = bot_id
        self.description = description
        self._loading = True
        self.name.set(profile["display_name"])
        self._refresh_choices()
        refs = [c["component_ref"] for c in description["components"]]
        if self.selected not in refs:
            self.selected = refs[0]
        self._load_component()
        self._loading = False
        self._dirty = False
        self.save_button.configure(state="disabled")
        self.status.set(f"{bot_id} · Saved profile")
        self.refresh_runtime()

    def _load_component(self):
        self.component = self.backend.get_component(self.bot_id, self.selected)
        descriptor = next(c for c in self.description["components"] if c["component_ref"] == self.selected)
        role_key = "motor" if self.selected.startswith("motor:") else self.selected
        role = self.catalog["roles"].get(role_key, {"options": [], "status": "unavailable"})
        self.options = role["options"]
        self.unavailable = role["status"] == "not_implemented"
        self.component_title.configure(text=descriptor["label"])
        supported = any(o["configuration"] == self.component["configuration"] and o["runtime_supported"]
                        for o in self.options)
        self.availability.configure(text="UNAVAILABLE · Not implemented" if self.unavailable else
                                    "Catalog configuration" if supported else "CUSTOM · Runtime support unverified")
        self.role_label.configure(text="Role: " + self.component["role"].replace("_", " ").title())
        self.configuration.set(self.component["configuration"])
        values = list(dict.fromkeys([self.component["configuration"],
                                    *[o["configuration"] for o in self.options if o["runtime_supported"]]]))
        self.config_box.configure(values=values, state="disabled" if self.unavailable else "readonly")
        self.precision.set(self.component["precision"])
        self.seed.set("" if self.component["seed"] is None else str(self.component["seed"]))
        self.enabled.set(self.component["enabled"])
        self.seed_entry.configure(state="disabled" if self.unavailable else "normal")
        self.enabled_check.configure(state="disabled" if self.unavailable or self.selected == "spinal_cord" else "normal")
        self._update_metadata()
        self.schematic.show(self.description["components"], self.selected)

    def _option(self):
        return next((o for o in self.options if o["configuration"] == self.configuration.get()), None)

    def _configuration_changed(self, _=None):
        option = self._option()
        if option:
            self.precision.set(option["precisions"][0])
            self.enabled.set(option["enabled"])
        self._update_metadata()

    def _update_metadata(self):
        option = self._option()
        current = (self.component if self.configuration.get() == self.component["configuration"]
                   else option or self.component)
        self.implementation_label.configure(text="Implementation: " + current["implementation"].upper())
        self.precision_box.configure(values=option["precisions"] if option else [self.component["precision"]],
                                     state="disabled" if self.unavailable else "readonly")
        self.topology = current["topology"]
        # A saved custom topology remains authoritative until another configuration is chosen.
        if self.configuration.get() == self.component["configuration"]:
            self.topology = self.component["topology"]
        values = [self.topology["inputs"], *self.topology["hidden"], self.topology["outputs"]]
        label = " → ".join(str(v) for v in values if v is not None)
        self.topology_title.configure(text="Topology  /  " + (label or self.topology["kind"].upper()))
        self.topology_summary.configure(text=self.topology["summary"])
        self._draw_topology()

    def _draw_topology(self):
        if hasattr(self, "topology"):
            draw_topology(self.topology_canvas, self.topology)

    def save(self):
        try:
            profile = self.backend.get_profile(self.bot_id)
            component = deepcopy(self.component)
            option = self._option()
            if option and self.configuration.get() != component["configuration"]:
                for field in ("configuration", "implementation", "topology"):
                    component[field] = deepcopy(option[field])
            component["seed"] = int(self.seed.get().strip()) if self.seed.get().strip() else None
            component["precision"] = self.precision.get()
            component["enabled"] = self.enabled.get()
            if self.selected.startswith("motor:"):
                profile["motors"] = [component if m["motor_id"] == component["motor_id"] else m
                                     for m in profile["motors"]]
            else:
                profile[self.selected] = component
            profile["display_name"] = self.name.get()
            self.backend.save_profile(profile)
            self.load_bot(self.bot_id)
            return True
        except (ValueError, OSError) as exc:
            self._error(exc)
            return False

    def _resolve_changes(self):
        if not self._dirty:
            return True
        answer = messagebox.askyesnocancel("Unsaved profile", "Save changes before continuing?",
                                          parent=self.root)
        if answer is None:
            return False
        return self.save() if answer else True

    def select_component(self, ref):
        if ref == self.selected or not self._resolve_changes():
            return
        try:
            self.selected = ref
            self.load_bot(self.bot_id)
            self.inspector_canvas.yview_moveto(0)
        except (ValueError, OSError) as exc:
            self._error(exc)

    def _switch_bot(self, _=None):
        target = self.bot_choice.get()
        if self._resolve_changes():
            try:
                self.load_bot(target)
            except (ValueError, OSError) as exc:
                self._error(exc)
        self.bot_choice.set(self.bot_id)

    def reload(self):
        if self._resolve_changes():
            try:
                self.load_bot(self.bot_id)
            except (ValueError, OSError) as exc:
                self._error(exc)

    def create_dialog(self):
        if not self._resolve_changes():
            return
        # Discard means discard the old draft even if creation is cancelled.
        try:
            self.load_bot(self.bot_id)
        except (ValueError, OSError) as exc:
            self._error(exc)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Create Bot Profile")
        dialog.configure(bg=PANEL)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="New bot", style="Title.TLabel").pack(anchor="w", pady=(0, 16))
        variables = {}
        for key, label, value in (("bot_id", "Stable Bot ID", ""),
                                  ("display_name", "Display name", ""),
                                  ("template_id", "Template", self.bot_id)):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(10, 4))
            variable = tk.StringVar(value=value)
            variables[key] = variable
            if key == "template_id":
                widget = ttk.Combobox(frame, textvariable=variable, state="readonly",
                                      values=self.selector.cget("values"), width=32)
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=35)
            widget.pack(fill="x")
            if key == "bot_id":
                widget.focus_set()
        ttk.Label(frame, text="ID: letters, digits, _ or -; start with a letter.\nCopies configuration only, not checkpoints.",
                  style="Muted.TLabel").pack(anchor="w", pady=16)
        error = ttk.Label(frame, text="", wraplength=340, foreground="#ffb28b")
        error.pack(fill="x")

        def create():
            try:
                result = self.backend.create_bot(variables["bot_id"].get(), variables["display_name"].get(),
                                                 template_id=variables["template_id"].get())
                self.load_bot(result["bot_id"])
                dialog.destroy()
            except (ValueError, OSError) as exc:
                error.configure(text=str(exc))

        ttk.Button(frame, text="Create bot", command=create, style="Accent.TButton").pack(side="right", pady=(12, 0))
        ttk.Button(frame, text="Cancel", command=dialog.destroy).pack(side="left", pady=(12, 0))
        dialog.bind("<Return>", lambda _: create())
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        # Expose only widgets, allowing the explicit graphical smoke to use real UI events.
        self.create_fields = variables
        self.create_window = dialog

    def refresh_runtime(self):
        try:
            state = self.backend.runtime_state(self.bot_id, int(self.level.get()))
            self.runtime = state
            checkpoints = state["checkpoints"]
            yesno = lambda key: "yes" if checkpoints[key] else "no"
            self.runtime_label.configure(text=f"{state['status'].upper()}  ·  Episodes: {state['episode_count']}\n"
                                         f"Planner checkpoint: {yesno('planner')}\n"
                                         f"Motor checkpoint: {yesno('motor_controller')}")
            self.runtime_summary.configure(text=f"{state['status'].upper()}   ·   Planner: {yesno('planner')}   ·   "
                                           f"Motor: {yesno('motor_controller')}   ·   Episodes: {state['episode_count']}")
            self.secondary_label.configure(text=f"Secondary training state\nCritic: {yesno('critic')}   ·   Optimizer: {yesno('optimizer')}")
        except (ValueError, OSError) as exc:
            self.runtime_label.configure(text=f"Runtime state unavailable: {exc}")
            self.runtime_summary.configure(text="Runtime unavailable · Enter a positive Training Set number")
            self.secondary_label.configure(text="")

    def _poll_runtime(self):
        self.refresh_runtime()
        self._runtime_job = self.root.after(5000, self._poll_runtime)

    def fullscreen(self, _=None):
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def close(self):
        if self._resolve_changes():
            if self._runtime_job:
                self.root.after_cancel(self._runtime_job)
            self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Graphical Bot Profile editor")
    parser.add_argument("--bot", default="player1")
    parser.add_argument("--profile-dir", help="Override profile storage for isolated experiments")
    parser.add_argument("--runtime-root", help="Override runtime storage")
    parser.add_argument("--windowed", action="store_true", help="Start in a resizable desktop window")
    args = parser.parse_args(argv)
    kwargs = {}
    if args.profile_dir:
        kwargs["profile_root"] = args.profile_dir
    if args.runtime_root:
        kwargs["runtime_root"] = args.runtime_root
    root = tk.Tk()
    try:
        BotProfiler(root, BotProfilerBackend(**kwargs), args.bot, maximize=not args.windowed)
    except (ValueError, OSError) as exc:
        messagebox.showerror("Bot Profiler could not start", str(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
