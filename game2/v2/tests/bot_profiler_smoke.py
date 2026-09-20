"""Explicit graphical smoke: python -m game2.v2.tests.bot_profiler_smoke.

Requires a desktop or Xvfb. Exercises widgets and real backend persistence in
an isolated directory; never edits the operator's profiles or checkpoints.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
from unittest.mock import patch

from game2.v2.management.bot_profiler import BotProfiler
from game2.v2.management.bot_profiler_backend import BotProfilerBackend


def main():
    with tempfile.TemporaryDirectory() as directory:
        backend = BotProfilerBackend(profile_root=Path(directory) / "bots", runtime_root=Path(directory) / "runtime")
        backend.save_profile(BotProfilerBackend().get_profile("player1"))
        original = backend.get_profile("player1")
        root = tk.Tk()
        app = BotProfiler(root, backend, maximize=False)
        try:
            root.update()
            for ref in ("cerebral_cortex", "spinal_cord", "motor:right", "motor:jump"):
                a, b, c, d = app.schematic.hitboxes[ref]
                app.schematic.event_generate("<Button-1>", x=int((a+c)/2), y=int((b+d)/2))
                root.update()
                assert app.selected == ref
                component = backend.get_component("player1", ref)
                assert app.topology == component["topology"]
                assert app.seed.get() == ("" if component["seed"] is None else str(component["seed"]))
                assert app.unavailable == (ref == "cerebral_cortex")
            app.create_dialog()
            root.update()
            app.create_fields["bot_id"].set("profiler_smoke")
            app.create_fields["display_name"].set("Smoke bot")
            app.create_window.event_generate("<Return>")
            root.update()
            assert app.bot_id == "profiler_smoke"
            app.name.set("Reflex experiment")
            app.seed.set("37")
            app.save_button.invoke()
            root.update()
            assert backend.get_component("profiler_smoke", "motor:jump")["seed"] == 37
            assert backend.get_profile("profiler_smoke")["display_name"] == "Reflex experiment"
            assert backend.get_profile("player1") == original
            saved = backend.get_profile("profiler_smoke")
            for invalid in ("not an integer", "1.5"):
                app.seed.set(invalid)
                with patch("tkinter.messagebox.showerror") as error:
                    assert not app.save()
                    error.assert_called_once()
                assert backend.get_profile("profiler_smoke") == saved
            with patch("tkinter.messagebox.askyesnocancel", return_value=None):
                app.select_component("spinal_cord")
                assert app.selected == "motor:jump"
            with patch("tkinter.messagebox.askyesnocancel", return_value=False):
                app.select_component("spinal_cord")
                assert not app._dirty
            app.bot_choice.set("player1")
            app.selector.event_generate("<<ComboboxSelected>>")
            root.update()
            assert app.bot_id == "player1"
            app.bot_choice.set("profiler_smoke")
            app.selector.event_generate("<<ComboboxSelected>>")
            root.update()
            app.select_component("motor:jump")
            assert app.seed.get() == "37"
            assert app.runtime["status"] == "fresh"
            state = backend.runtime_state("profiler_smoke", 2)
            checkpoints = Path(state["checkpoint_dir"])
            checkpoints.mkdir(parents=True)
            (checkpoints / "planner.pt").touch()
            app.level.set("2")
            app.refresh_runtime()
            assert app.runtime["status"] == "partial"
            for name in ("motor.pt", "critic.pt", "optimizer.pt"):
                (checkpoints / name).touch()
            app.refresh_runtime()
            assert app.runtime["status"] == "ready"
            # Additional anatomical components remain selectable without GUI changes.
            profile = backend.get_profile("profiler_smoke")
            for name in ("left_leg", "right_leg", "left_arm", "right_arm", "balance", "custom_reflex"):
                motor = deepcopy(profile["motors"][0])
                motor["motor_id"] = name
                profile["motors"].append(motor)
            backend.save_profile(profile)
            app.load_bot("profiler_smoke")
            root.update()
            assert len(app.schematic.hitboxes) == len(app.description["components"])
            app.select_component("motor:custom_reflex")
            assert app.selected == "motor:custom_reflex"
            for geometry in ("1000x700", "1280x800", "1600x900"):
                root.geometry(geometry)
                root.update()
                assert app.inspector_canvas.winfo_width() >= 300
                boxes = list(app.schematic.hitboxes.values())
                for i, (a, b, c, d) in enumerate(boxes):
                    for e, f, g, h in boxes[i+1:]:
                        assert c <= e or g <= a or d <= f or h <= b
                try:
                    from PIL import ImageGrab
                    ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                        root.winfo_rootx()+root.winfo_width(),
                                        root.winfo_rooty()+root.winfo_height())).save(
                                            f"/tmp/bot-profiler-{geometry}.png")
                except ImportError:
                    pass
            app.load_bot("player1")
            app.select_component("motor:jump")
            root.attributes("-fullscreen", True)
            root.update()
            root.after(300, root.quit)
            root.mainloop()
            # Optional review artifact; Pillow is not a GUI/runtime dependency.
            try:
                from PIL import ImageGrab
                ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                    root.winfo_rootx()+root.winfo_width(),
                                    root.winfo_rooty()+root.winfo_height())).save("/tmp/bot-profiler-smoke.png")
            except ImportError:
                pass
            print("PASS: callouts, topology, create, isolation, save/reload, invalid drafts, switching, runtime and resize")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
