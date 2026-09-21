"""Terminal presentation of child training events (the child need not be a TTY)."""
from __future__ import annotations

import json
import shutil
import time


class TrainingDisplay:
    def __init__(self, output, clock=time.monotonic):
        self.output = output
        self.clock = clock
        self.tty = bool(getattr(output, "isatty", lambda: False)())
        self.active = False
        self.last_refresh = float("-inf")
        self.phase = None
        self.attempt = 0

    def close(self):
        if self.active:
            self.output.write("\r\x1b[2K")
            self.active = False
        self.output.flush()

    def line(self, text):
        self.close()
        self.output.write(text + "\n")
        self.output.flush()

    def live(self, phase, text):
        now = self.clock()
        changed = phase != self.phase
        if not changed and now - self.last_refresh < (0.2 if self.tty else 2.0):
            return
        self.phase = phase
        self.last_refresh = now
        if self.tty:
            width = shutil.get_terminal_size(fallback=(100, 24)).columns
            text = text[:max(1, width - 1)]
            self.output.write("\r\x1b[2K" + text)
            self.active = True
            self.output.flush()
        else:
            self.line(text)

    @staticmethod
    def bar(fraction):
        count = round(20 * max(0.0, min(1.0, fraction)))
        return "[" + "█" * count + "·" * (20 - count) + "]"

    def consume(self, line):
        prefix, _, body = line.strip().partition(" ")
        if prefix not in {"LEARNING", "ROLLOUT", "PPO", "PROGRESS", "TRAIN_RESULT",
                          "EVALUATION", "FINAL_CHECK", "FINAL_EVALUATION"}:
            if line.startswith("MAP ") and "starting" in line:
                self.line("")
            self.line(line.rstrip())
            return
        event = json.loads(body)
        attempt = int(event.get("attempt", event.get("attempts", self.attempt)))
        budget = event.get("max_attempts", "?")
        if prefix == "LEARNING":
            if event["status"] == "start":
                self.attempt = attempt
                self.phase = None
                self.line(f"Run {attempt}/{budget} · collecting experience")
            elif event["status"] == "update":
                self.line(f"Update {attempt}/{budget} · preparing PPO")
            elif event["status"] == "done":
                self.line(
                    f"Update {attempt}/{budget} · "
                    f"{'saved' if event['updated'] else 'skipped'} · "
                    f"{float(event['seconds']):.1f}s"
                )
        elif prefix == "ROLLOUT":
            tick, limit = int(event["world_tick"]), int(event["episode_limit"])
            speed = float(event.get("ticks_per_second", 0.0))
            eta = f" · ETA {max(0, limit-tick)/speed:.0f}s" if speed > 0 else ""
            label = "Run" if event["mode"] == "train" else "Verify"
            title = f"{label} {attempt}/{budget}"
            if event.get("final_check"):
                title = f"Final check {event['map_id']}"
            self.live((label, event["episode_id"]),
                      f"{title} {self.bar(tick / limit)} "
                      f"{tick}/{limit} ticks · goal {100*float(event['progress']):.1f}%"
                      f" · {speed:.0f}t/s{eta}")
        elif prefix == "PPO":
            step, steps = int(event["step"]), int(event["steps"])
            self.live(("ppo", event["episode_id"]),
                      f"Update {attempt}/{budget} {self.bar(step / steps)} "
                      f"{step}/{steps} batches · loss {float(event['loss']):.4f}")
        elif prefix == "TRAIN_RESULT":
            previous = event.get("previous_progress")
            trend = "" if previous is None else (
                f" · {100*(float(event['progress'])-float(previous)):+.1f} pp vs previous"
            )
            self.line(
                f"Run {attempt}/{budget} · {str(event['result']).upper()} · "
                f"goal {100*float(event['progress']):.1f}%{trend}"
            )
        elif prefix == "PROGRESS":
            self.line(f"Attempt total {float(event['seconds']):.1f}s · "
                      f"training successes {event['successes']}/{attempt}")
        elif prefix == "FINAL_CHECK":
            status = event["status"]
            if status == "start":
                self.line("Final check · all training maps · frozen model")
            else:
                self.line(
                    "Final check · " + (
                        "PASS" if status == "pass" else "FAIL"
                    )
                )
        else:
            passed = event["result"] == "success"
            label = (
                f"Final check {event.get('map_id', '')}".rstrip()
                if prefix == "FINAL_EVALUATION"
                else "Verify"
            )
            verification_index = event.get("verification_index")
            verification_required = event.get("verification_required")
            streak = (
                f" {verification_index}/{verification_required}"
                if verification_index is not None
                and verification_required is not None
                else ""
            )
            self.line(
                f"{label}{streak} · {'PASS' if passed else 'FAIL'}"
                f" · goal {100*float(event.get('progress', 0)):.1f}% · learning OFF"
            )
