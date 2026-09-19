"""Asynchronous latest-only inference for learned Player runtimes."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from game2.v2.contracts.vision import VisionGrid

from .runtime import DecisionSample


@dataclass(frozen=True)
class InferenceSnapshot:
    """The most recently completed inference operation."""

    serial: int
    result_tick: int | None
    sample: DecisionSample | None


class InferenceWorker:
    """Run one Player model on a latest-only Vision mailbox.

    The worker never overlaps calls to ``process_grid``.  A submitted grid
    replaces an older pending grid while the model is busy, so inference
    follows the newest available observation rather than building a queue.
    """

    def __init__(self, player):
        if not callable(getattr(player, "process_grid", None)):
            raise TypeError("inference worker requires a Player with process_grid")
        self.player = player
        self._condition = threading.Condition()
        self._pending: VisionGrid | None = None
        self._latest_sample: DecisionSample | None = None
        self._result_tick: int | None = None
        self._serial = 0
        self._error: BaseException | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="v2-learned-inference",
                                        daemon=True)
        self._thread.start()

    @property
    def latest_sample(self) -> DecisionSample | None:
        with self._condition:
            return self._latest_sample

    @property
    def latest(self) -> DecisionSample | None:
        return self.latest_sample

    @property
    def result_tick(self) -> int | None:
        with self._condition:
            return self._result_tick

    @property
    def latest_result_tick(self) -> int | None:
        return self.result_tick

    @property
    def result_serial(self) -> int:
        with self._condition:
            return self._serial

    @property
    def error(self) -> BaseException | None:
        with self._condition:
            return self._error

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def joined(self) -> bool:
        return not self._thread.is_alive()

    def snapshot(self) -> InferenceSnapshot:
        with self._condition:
            return InferenceSnapshot(self._serial, self._result_tick,
                                     self._latest_sample)

    def submit(self, grid: VisionGrid) -> None:
        """Replace the pending grid with ``grid`` for the worker."""
        if not isinstance(grid, VisionGrid):
            raise TypeError("inference worker requires a VisionGrid")
        with self._condition:
            if self._closed:
                raise RuntimeError("inference worker is closed")
            if self._error is not None:
                raise RuntimeError("learned inference failed") from self._error
            self._pending = grid
            self._condition.notify()
        # Let a ready worker take the grid without imposing an inference wait
        # on the action loop.  This also keeps injected-clock tests cooperative.
        time.sleep(0)

    def wait_for_change(self, serial: int, timeout: float | None = None) -> InferenceSnapshot:
        """Wait until a result changes, the worker fails, or it closes."""
        if type(serial) is not int or serial < 0:
            raise ValueError("inference result serial must be a non-negative integer")
        if timeout is not None and timeout < 0:
            raise ValueError("inference wait timeout must be non-negative")
        with self._condition:
            if self._serial == serial and self._error is None and not self._closed:
                self._condition.wait(timeout)
            return InferenceSnapshot(self._serial, self._result_tick,
                                     self._latest_sample)

    def raise_if_failed(self) -> None:
        error = self.error
        if error is not None:
            raise RuntimeError("learned inference failed") from error

    def close(self) -> None:
        """Stop accepting work and join the model thread before returning."""
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                grid = self._pending
                self._pending = None
            assert grid is not None
            try:
                sample = self.player.process_grid(grid)
            except BaseException as exc:
                with self._condition:
                    if not self._closed:
                        self._error = exc
                        self._pending = None
                        self._condition.notify_all()
                return
            with self._condition:
                if self._closed:
                    return
                self._latest_sample = sample
                self._result_tick = grid.world_tick
                self._serial += 1
                self._condition.notify_all()


__all__ = ["InferenceSnapshot", "InferenceWorker"]
