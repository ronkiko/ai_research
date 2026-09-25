"""Bounded latest-frame stream. Slow consumers intentionally lose intermediate frames."""
from __future__ import annotations
from collections import deque
import copy
import threading
from .contracts import validate_render_frame

class FrameHub:
    def __init__(self, limit: int = 4):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("frame queue limit must be within [1,32]")
        self.limit, self.condition = limit, threading.Condition()
        self.events, self.next_id = deque(maxlen=limit), 1
        self.active_epoch, self.last_revision, self.last_tick, self.last_frame_id = None, -1, -1, None

    def publish(self, frame: dict) -> dict | None:
        frame = copy.deepcopy(validate_render_frame(frame))
        epoch, revision, tick = frame["source_world_epoch"], frame["source_world_revision"], frame["source_world_tick"]
        with self.condition:
            if frame["frame_id"] == self.last_frame_id:
                return None
            reset = False
            if self.active_epoch is None:
                self.active_epoch = epoch
            elif epoch != self.active_epoch:
                if frame["freshness"].get("previous_epoch") != self.active_epoch:
                    return None
                self.active_epoch, self.last_revision, self.last_tick = epoch, -1, -1
                self.events.clear()
                reset = True
            if revision < self.last_revision or (revision == self.last_revision and tick <= self.last_tick):
                return None
            item = {"id":self.next_id,"event":"frame.latest","data":{"frame":frame,"reset":reset}}
            self.next_id += 1
            self.events.append(item)
            self.last_revision, self.last_tick, self.last_frame_id = revision, tick, frame["frame_id"]
            self.condition.notify_all()
            return copy.deepcopy(item)

    def since(self, last_id: int) -> list[dict]:
        with self.condition:
            ready = [item for item in self.events if item["id"] > last_id]
            return [] if not ready else [copy.deepcopy(ready[-1])]

    def wait(self, last_id: int, timeout: float = 10.0) -> list[dict]:
        with self.condition:
            ready = [item for item in self.events if item["id"] > last_id]
            if not ready:
                self.condition.wait(timeout)
                ready = [item for item in self.events if item["id"] > last_id]
            return [] if not ready else [copy.deepcopy(ready[-1])]

__all__ = ["FrameHub"]
