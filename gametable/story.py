"""Durable first-day story flow, escort consent, timer and day-start placement."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from gameclient.v1.clients.base import HostClient, HostClientError
from gameserver.v1.common.config import EMBODIED_WORLD_PORT, HOST
from gameserver.v1.common.protocol import message, rpc
from organism.controllers.scripted_escort import EscortError, ScriptedEscortController
from organism.lease import BodyLease, BodyLeaseBusy


TERMINAL_RECEIPTS = {"applied", "arrived", "blocked", "failed", "cancelled", "uncertain"}
ESCORT_TERMINAL = {"scripted_arrival", "cancelled", "blocked", "failed"}


class StoryFlowError(RuntimeError):
    pass


class StoryFlow:
    def __init__(
        self,
        store,
        *,
        events=None,
        gate_path: str | Path | None = None,
        intro_seconds: float | None = None,
        body_lease: BodyLease | None = None,
        escort: ScriptedEscortController | None = None,
        world_rpc=rpc,
    ):
        self.store = store
        self.events = events or (lambda _name, _payload: None)
        self.gate_path = Path(
            gate_path
            or os.environ.get(
                "DIRECTOR_MANUAL_GATE",
                Path(__file__).resolve().parents[1] / "runtime" / "director-manual.json",
            )
        )
        configured = os.environ.get("GAMETABLE_INTRO_SECONDS")
        self.intro_seconds = float(
            intro_seconds if intro_seconds is not None
            else configured if configured is not None
            else 300.0
        )
        if not 0.05 <= self.intro_seconds <= 3600.0:
            raise ValueError("intro_seconds must be within [0.05,3600]")
        self.body_lease = body_lease or BodyLease()
        self.world_rpc = world_rpc
        self._lock = threading.RLock()
        self._presence: set[str] = set()
        self._active_since: float | None = None
        self._runtime = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_checkpoint = 0.0
        self._last_offer_try = 0.0
        self._last_escort_persist = 0.0
        self._last_escort_key = None
        self._director_clients: dict[str, HostClient] = {}
        self.escort = escort or ScriptedEscortController(
            body_lease=self.body_lease,
            world_rpc=self.world_rpc,
            on_update=self._on_escort_update,
        )
        self._write_gate(False, None)
        self._normalize_recovered_state()

    def _normalize_recovered_state(self):
        story = self.store.story_state()
        escort = story.get("escort") or {}
        if escort.get("status") == "reconciling":
            self._write_gate(False, escort.get("job_id"))

    def attach_runtime(self, runtime):
        self._runtime = runtime
        runtime.attach_story(self)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="gametable-story-flow"
        )
        self._thread.start()

    def close(self):
        self._stop.set()
        self._checkpoint_elapsed(force=True)
        try:
            record = self.escort.pause_for_restart()
            if record is not None:
                self._on_escort_update(record, force=True)
        except Exception:
            pass
        self._write_gate(False, None)
        for source_id in list(self._director_clients):
            self.director_disconnect(source_id)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def snapshot(self) -> dict[str, Any]:
        story = self.store.story_state()
        intro = story.get("intro") or {}
        elapsed = float(intro.get("elapsed_active_seconds", 0.0))
        with self._lock:
            if self._active_since is not None and intro.get("timer_started"):
                elapsed += max(0.0, time.monotonic() - self._active_since)
            presence = len(self._presence)
        story["intro"] = {**intro, "elapsed_active_seconds": round(elapsed, 3)}
        story["ui_sessions"] = presence
        story["presentation_mode"] = (
            "world_control"
            if (story.get("escort") or {}).get("status") == "escort_active"
            else "vn_dialogue"
        )
        return story

    def presence_open(self, session_id: str):
        with self._lock:
            before = bool(self._presence)
            self._presence.add(session_id)
            if not before and self._presence and self._timer_should_run():
                self._active_since = time.monotonic()

    def presence_close(self, session_id: str):
        with self._lock:
            self._presence.discard(session_id)
            if not self._presence:
                self._checkpoint_elapsed_locked(force=True)
                self._active_since = None

    def _timer_should_run(self) -> bool:
        story = self.store.story_state()
        intro = story.get("intro") or {}
        return (
            story.get("day_id") == 1
            and intro.get("timer_started") is True
            and intro.get("offer_due") is not True
            and intro.get("offer_published") is not True
        )

    def note_director_message(self, event):
        story = self.store.story_state()
        intro = story.get("intro") or {}
        if story.get("day_id") != 1 or intro.get("timer_started"):
            return
        intro["timer_started"] = True
        intro["phase"] = "intro_dialogue"
        story["intro"] = intro
        self.store.set_story_state(
            story, expected_revision=story.get("story_revision")
        )
        with self._lock:
            if self._presence and self._active_since is None:
                self._active_since = time.monotonic()
        self.events("story.intro_started", {
            "offer_id": intro.get("offer_id"),
            "threshold_seconds": self.intro_seconds,
        })

    def _checkpoint_elapsed_locked(self, *, force=False):
        if self._active_since is None:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint < 1.0:
            return
        delta = max(0.0, now - self._active_since)
        if delta <= 0:
            return
        story = self.store.story_state()
        intro = story.get("intro") or {}
        if not self._timer_should_run():
            self._active_since = None
            return
        intro["elapsed_active_seconds"] = (
            float(intro.get("elapsed_active_seconds", 0.0)) + delta
        )
        story["intro"] = intro
        try:
            self.store.set_story_state(
                story, expected_revision=story.get("story_revision")
            )
        except ValueError:
            return
        self._active_since = now
        self._last_checkpoint = now

    def _checkpoint_elapsed(self, *, force=False):
        with self._lock:
            self._checkpoint_elapsed_locked(force=force)

    def _mark_offer_due_if_ready(self):
        self._checkpoint_elapsed()
        story = self.store.story_state()
        intro = story.get("intro") or {}
        if (
            story.get("day_id") != 1
            or intro.get("timer_started") is not True
            or intro.get("offer_due") is True
            or intro.get("offer_published") is True
        ):
            return
        elapsed = float(intro.get("elapsed_active_seconds", 0.0))
        with self._lock:
            if self._active_since is not None:
                elapsed += max(0.0, time.monotonic() - self._active_since)
        if elapsed < self.intro_seconds:
            return
        with self._lock:
            self._checkpoint_elapsed_locked(force=True)
            self._active_since = None
        story = self.store.story_state()
        intro = story["intro"]
        if intro.get("offer_due") or intro.get("offer_published"):
            return
        intro.update(
            offer_due=True,
            phase="escort_offer_pending",
        )
        story["intro"] = intro
        self.store.set_story_state(
            story, expected_revision=story.get("story_revision")
        )
        self.events("story.offer_due", {
            "offer_id": intro["offer_id"], "day_id": 1
        })

    def _try_publish_offer(self):
        if self._runtime is None:
            return
        story = self.store.story_state()
        intro = story.get("intro") or {}
        if not intro.get("offer_due") or intro.get("offer_published"):
            return
        now = time.monotonic()
        if now - self._last_offer_try < 1.0:
            return
        self._last_offer_try = now
        self._runtime.submit_story_offer(intro["offer_id"])

    def _loop(self):
        while not self._stop.wait(0.2):
            try:
                self._mark_offer_due_if_ready()
                self._try_publish_offer()
                self._reconcile_day_start()
            except Exception:
                # The authoritative state remains durable; next loop reconciles.
                pass

    def respond(self, *, offer_id: str, response: str, text: str) -> dict[str, Any]:
        if response not in {"accept", "decline", "clarify"}:
            raise StoryFlowError("response must be accept, decline, or clarify")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            raise StoryFlowError("response text is required")
        story = self.store.story_state()
        intro = story.get("intro") or {}
        if (
            intro.get("offer_id") != offer_id
            or intro.get("offer_published") is not True
        ):
            raise StoryFlowError("escort offer is not currently published")
        current = intro.get("response")
        escort_state = story.get("escort") or {}
        retryable_accept = (
            current == "accept"
            and response == "accept"
            and escort_state.get("status") in {"blocked", "failed", "reconciling"}
        )
        if current is not None and not retryable_accept:
            if current == response:
                return self.snapshot()
            raise StoryFlowError("escort offer already has a different response")

        if not retryable_accept:
            self.store.publish_story_message(
                f"dialogue.{offer_id}.director.{response}",
                "director",
                text.strip(),
                turn_id=f"story.{offer_id}",
            )
        intro["response"] = response
        if response == "decline":
            intro["phase"] = "escort_declined"
            story["intro"] = intro
            self.store.set_story_state(
                story, expected_revision=story.get("story_revision")
            )
            self._write_gate(False, None)
            self.events("story.escort_declined", {"offer_id": offer_id})
            return self.snapshot()
        if response == "clarify":
            intro["response"] = None
            intro["phase"] = "escort_offer_published"
            story["intro"] = intro
            self.store.set_story_state(
                story, expected_revision=story.get("story_revision")
            )
            self.events("story.escort_clarify", {"offer_id": offer_id})
            return self.snapshot()

        intro["phase"] = "escort_starting"
        story["intro"] = intro
        story["escort"] = {
            **(story.get("escort") or {}),
            "status": "escort_starting",
            "offer_id": offer_id,
            "reason": None,
        }
        story = self.store.set_story_state(
            story, expected_revision=story.get("story_revision")
        )
        try:
            record = self.escort.start(
                day_id=int(story["day_id"]),
                offer_id=offer_id,
            )
        except EscortError as exc:
            story = self.store.story_state()
            story["escort"] = {
                **(story.get("escort") or {}),
                "status": "blocked",
                "phase": "released",
                "reason": str(exc)[:400],
            }
            story["intro"]["phase"] = "escort_start_failed"
            story["intro"]["response"] = "accept"
            self.store.set_story_state(
                story, expected_revision=story.get("story_revision")
            )
            self._write_gate(False, None)
            raise StoryFlowError(str(exc)) from exc
        self._on_escort_update(record, force=True)
        return self.snapshot()

    def _write_gate(
        self, enabled: bool, escort_id: str | None, day_id: int | None = None
    ):
        self.gate_path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "version": 1,
            "enabled": bool(enabled),
            "escort_id": escort_id,
            "day_id": day_id,
            "updated_at": time.time(),
        }
        fd, name = tempfile.mkstemp(
            prefix=self.gate_path.name + ".", dir=self.gate_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.gate_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _on_escort_update(self, record: dict[str, Any], force=False):
        key = (record.get("status"), record.get("phase"), record.get("reason"))
        now = time.monotonic()
        if (
            not force
            and key == self._last_escort_key
            and now - self._last_escort_persist < 0.5
        ):
            return
        self._last_escort_key = key
        self._last_escort_persist = now
        story = self.store.story_state()
        story["escort"] = {
            **(story.get("escort") or {}),
            **copy.deepcopy(record),
        }
        status = record.get("status")
        if status == "escort_active":
            story["intro"]["phase"] = "escort_active"
            self._write_gate(
                True, record.get("job_id"), int(story.get("day_id", 1))
            )
        elif status == "scripted_arrival":
            story["intro"]["phase"] = "arrived"
            self._write_gate(False, record.get("job_id"))
        elif status in ESCORT_TERMINAL:
            self._write_gate(False, record.get("job_id"))
        try:
            self.store.set_story_state(
                story, expected_revision=story.get("story_revision")
            )
        except ValueError:
            return
        self.events("story.escort_updated", {
            "job_id": record.get("job_id"),
            "status": status,
            "phase": record.get("phase"),
        })

    def _director_client(self, source_id: str) -> HostClient:
        if not isinstance(source_id, str) or not source_id:
            raise StoryFlowError("source_id is required")
        with self._lock:
            client = self._director_clients.get(source_id)
            if client is None:
                client = HostClient(
                    "browser." + source_id[:40],
                    port=17701,
                    timeout=1.0,
                )
                self._director_clients[source_id] = client
            return client

    def director_acquire(self, source_id: str, *, transfer=False) -> dict:
        return self._director_client(source_id).control_acquire(
            transfer=transfer
        )

    def director_input(self, source_id: str, lease_id: str, move_x: int) -> dict:
        return self._director_client(source_id).input(
            move_x, lease_id=lease_id
        )

    def director_release(self, source_id: str, lease_id: str) -> dict:
        client = self._director_client(source_id)
        try:
            return client.control_release(lease_id)
        finally:
            self.director_disconnect(source_id, release=False)

    def director_disconnect(self, source_id: str, *, release=True) -> None:
        with self._lock:
            client = self._director_clients.pop(source_id, None)
        if client is None:
            return
        client.close()

    def on_turn_completed(self, event: dict, audit: dict):
        if event.get("intent_id") != "request_sleep":
            return
        contract = audit.get("contract") or {}
        decision = contract.get("decision") or {}
        effects = (contract.get("effect_plan") or {}).get("state_effects") or []
        if decision.get("disposition") != "accept":
            return
        if not any(item.get("type") == "sleep" for item in effects if isinstance(item, dict)):
            return
        self.schedule_next_day()

    def schedule_next_day(self):
        if self._runtime is not None:
            self._runtime.cancel_active_actions("sleep_day_start")
        try:
            self.escort.cancel("sleep_day_start")
        except Exception:
            pass
        self._write_gate(False, None)
        story = self.store.story_state()
        if story.get("day_phase") == "waking":
            return story
        next_day = int(story.get("day_id", 1)) + 1
        day_start_id = f"day-start.{next_day}"
        story["day_phase"] = "waking"
        story["day_start_id"] = day_start_id
        story["day_start_placement"] = {
            "status": "pending",
            "zone_id": "hallway",
            "spawn_id": "yuki_day_start",
            "receipt": None,
        }
        return self.store.set_story_state(
            story, expected_revision=story.get("story_revision")
        )

    def _world(self, kind: str, **fields) -> dict:
        value = self.world_rpc(
            HOST, EMBODIED_WORLD_PORT, message(kind, **fields), 0.75
        )
        if value.get("type") == "error":
            raise StoryFlowError(
                str(value.get("error") or "world rejected story request")
            )
        return value

    def _wait_receipt(self, action_id: str, timeout=3.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = self._world("receipt", action_id=action_id).get("receipt")
            if isinstance(receipt, dict) and receipt.get("status") in TERMINAL_RECEIPTS:
                return receipt
            time.sleep(0.01)
        raise StoryFlowError("day-start placement receipt timed out")

    def _reconcile_day_start(self):
        story = self.store.story_state()
        placement = story.get("day_start_placement") or {}
        if story.get("day_phase") != "waking" or placement.get("status") not in {
            "pending", "reconciling",
        }:
            return
        try:
            lease = self.body_lease.acquire(
                story["day_start_id"], "day_start_placement"
            )
        except BodyLeaseBusy:
            return
        try:
            response = self._world(
                "day_start",
                request_id=f"story.{story['day_start_id']}",
                entity_id=os.environ.get("EMBODIED_ENTITY_ID", "entity.yuki"),
                day_start_id=story["day_start_id"],
                zone_id="hallway",
                spawn_id="yuki_day_start",
                capability="story_day_start",
            )
            queued = response.get("receipt")
            if not isinstance(queued, dict):
                raise StoryFlowError("world returned no day-start receipt")
            receipt = self._wait_receipt(str(queued["action_id"]))
            if receipt.get("status") != "applied":
                placement.update(
                    status="reconciling",
                    receipt=receipt,
                )
                story["day_start_placement"] = placement
                self.store.set_story_state(
                    story, expected_revision=story.get("story_revision")
                )
                return
            next_day = int(story["day_start_id"].rsplit(".", 1)[1])
            story["day_id"] = next_day
            story["day_phase"] = "awake"
            placement.update(status="applied", receipt=receipt)
            story["day_start_placement"] = placement
            if next_day > 1:
                story["intro"]["phase"] = "complete"
                story["intro"]["timer_started"] = False
                story["intro"]["offer_due"] = False
                story["intro"]["offer_published"] = False
            story = self.store.set_story_state(
                story, expected_revision=story.get("story_revision")
            )
            try:
                self.store.append_story_memory({
                    "memory_id": story["day_start_id"],
                    "kind": "day_start",
                    "day_id": next_day,
                    "placement": {
                        "zone_id": "hallway",
                        "spawn_id": "yuki_day_start",
                        "learned_success": False,
                    },
                })
            except ValueError:
                pass
            self.events("story.day_started", {
                "day_id": next_day,
                "day_start_id": story["day_start_id"],
            })
        finally:
            lease.release()


__all__ = ["StoryFlow", "StoryFlowError"]
