"""UI-independent operator state, draft configuration and asynchronous commands."""

from collections import deque
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

from session import (
    SessionController,
    Statistics,
    SessionConfig,
    discover_levels,
    load_settings,
    save_settings,
    POLICIES,
    ROOT,
)

MODE_VALUES = {
    "human": ("Human", "Play"),
    "train": ("Bot", "Training"),
    "play": ("Bot", "Play"),
}


class LabPresenter:
    def __init__(self, settings_path):
        self.settings_path = Path(settings_path)
        self.levels = discover_levels()
        self.draft = load_settings(self.settings_path)
        self.numbers = dict(
            episodes=str(self.draft.episodes),
            auto_speed=f"{self.draft.auto_speed:g}",
            seed=str(self.draft.seed),
        )
        self.controller = SessionController()
        self.statistics = Statistics()
        self.snapshot = None
        self.history = deque(maxlen=100)
        self.messages = deque(maxlen=30)
        self.error = ""
        self.notice = ""
        self.advanced = False
        self.closing = False
        self._worker = None
        self._human_results = deque(maxlen=100)
        self._human_seen = deque(maxlen=100)
        self._human_successes = self._human_attempts = 0
        self._started_at = None
        self.elapsed = 0.0
        self.export_path = None

    @property
    def busy(self):
        return self._worker is not None and self._worker.is_alive()

    @property
    def mode(self):
        return (
            "human"
            if self.draft.controller == "Human"
            else ("train" if self.draft.bot_mode == "Training" else "play")
        )

    @property
    def active(self):
        return self.controller.config

    @property
    def active_human(self):
        return (
            self.active is not None
            and self.active.controller == "Human"
            and self.controller.status == "Running"
        )

    def set_mode(self, mode):
        controller, bot_mode = MODE_VALUES[mode]
        self.draft = replace(self.draft, controller=controller, bot_mode=bot_mode)
        if mode != "train":
            self.draft.execution = "Realtime"
        self.error = ""

    def config(self):
        result = replace(self.draft)
        if self.mode != "human":
            try:
                result.episodes = int(self.numbers["episodes"])
                result.seed = int(self.numbers["seed"])
                if self.mode == "train" and result.execution == "Auto":
                    result.auto_speed = float(self.numbers["auto_speed"])
            except ValueError as error:
                raise ValueError(
                    "Проверьте число эпизодов, seed и скорость: нужны числа."
                ) from error
        return result

    @property
    def dirty(self):
        if self.active is None:
            return False
        try:
            return self.config() != self.active
        except ValueError:
            return True

    def checkpoint_label(self):
        path = self.draft.checkpoint_path()
        return path, path.is_file()

    def message(self, text, error=False):
        self.messages.append((time.strftime("%H:%M:%S"), str(text), error))
        if error:
            self.error = str(text)
        else:
            self.notice = str(text)

    def _command(self, operation):
        if self.busy or self.closing:
            return False

        def work():
            try:
                operation()
            except Exception as error:
                self.controller.status_channel.publish(
                    {"type": "error", "message": str(error)}
                )

        self._worker = threading.Thread(
            target=work, name="cockpit-command", daemon=True
        )
        self._worker.start()
        return True

    def start(self):
        if self.busy or self.closing:
            return False
        self.controller.set_human_action(right=False)
        try:
            config = self.config()
            config.validate(levels=self.levels)
        except (OSError, ValueError) as error:
            self.message(str(error), error=True)
            return False
        self.error = ""
        self.draft = replace(config)

        def apply():
            if self.controller.apply(config):
                try:
                    save_settings(config, self.settings_path)
                except OSError as error:
                    self.controller.status_channel.publish(
                        {"type": "error", "message": f"Настройки не сохранены: {error}"}
                    )

        return self._command(apply)

    def stop(self):
        self.controller.set_human_action(right=False)
        return self._command(self.controller.stop)

    def poll(self):
        self.consume(self.controller.status_channel.drain())
        if self._started_at is not None:
            self.elapsed = time.monotonic() - self._started_at
            if self.controller.status in ("Finished", "Stopped", "Error"):
                self._started_at = None

    def consume(self, events):
        for event in events:
            kind = event.get("type")
            if kind == "session_started":
                self.statistics = Statistics()
                self.snapshot = None
                self.history.clear()
                self._human_results.clear()
                self._human_seen.clear()
                self._human_successes = self._human_attempts = 0
                self._started_at = time.monotonic()
                self.elapsed = 0.0
                self.error = ""
                self.export_path = None
                self.message("Эксперимент запущен")
            elif kind == "snapshot":
                self.snapshot = event
                self.statistics.update(event.get("metadata", {}))
            elif kind in ("live_stats", "episode_finished"):
                self.statistics.update(event)
                if kind == "episode_finished":
                    self.history.append(dict(event))
            elif kind == "auto_summary":
                self.statistics.update(event)
                self.message(
                    "Auto summary: "
                    + " ".join(
                        f"{key}={value}" for key, value in event.items() if key != "type"
                    )
                )
            elif (
                kind == "game_event"
                and self.active is not None
                and self.active.controller == "Human"
            ):
                key = (event.get("episode"), event.get("event"))
                if (
                    event.get("event") not in ("die", "success")
                    or key in self._human_seen
                ):
                    continue
                self._human_seen.append(key)
                success = event["event"] == "success"
                tick = event.get("tick", 0)
                self._human_results.append((success, tick))
                self._human_attempts += 1
                self._human_successes += int(success)
                window = len(self._human_results)
                record = dict(
                    event,
                    result=event["event"],
                    attempts=self._human_attempts,
                    successes=self._human_successes,
                    success_rate_total=self._human_successes / self._human_attempts,
                    episodes_window=window,
                    successes_100=sum(s for s, _ in self._human_results),
                    success_rate_100=sum(s for s, _ in self._human_results) / window,
                    mean_terminal_tick_100=sum(t for _, t in self._human_results)
                    / window,
                )
                self.statistics.update(record)
                self.history.append(record)
            elif kind == "error":
                self.message(event.get("message", "Ошибка сессии"), error=True)
                if event.get("details"):
                    self.messages.append(
                        (time.strftime("%H:%M:%S"), event["details"], True)
                    )
            elif kind == "checkpoint_saved":
                self.message("Веса сохранены: " + event["path"])
            elif kind == "session_finished":
                self.message("Серия завершена. Результаты сохранены на экране.")
            elif kind == "session_stopped":
                self.message("Эксперимент остановлен")
            elif kind == "notice":
                self.message(event.get("message", ""))

    def export_results(self, directory=None):
        if not self.statistics.attempts:
            self.message("Сначала завершите хотя бы одну попытку.", error=True)
            return None
        from dataclasses import asdict

        directory = (
            Path(directory) if directory is not None else ROOT / "runs" / "cockpit"
        )
        directory.mkdir(parents=True, exist_ok=True)
        config = asdict(self.active) if self.active else {}
        config["level"] = str(config.get("level", ""))
        path = (
            directory
            / f'result-{time.strftime("%Y%m%d-%H%M%S")}-{time.time_ns()%1000000:06d}.json'
        )
        path.write_text(
            json.dumps(
                dict(
                    config=config,
                    statistics=asdict(self.statistics),
                    elapsed_seconds=round(self.elapsed, 3),
                    recent_episodes=list(self.history),
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.export_path = path
        self.message("Отчёт сохранён: " + str(path))
        return path

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.controller.set_human_action(right=False)
        previous = self._worker

        def shutdown():
            if previous is not None:
                previous.join()
            try:
                self.controller.exit()
                save_settings(self.config(), self.settings_path)
            except Exception as error:
                self.controller.status_channel.publish(
                    {"type": "error", "message": str(error)}
                )

        self._worker = threading.Thread(
            target=shutdown, name="cockpit-shutdown", daemon=True
        )
        self._worker.start()
