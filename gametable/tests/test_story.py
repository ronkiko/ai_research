from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gametable.roleplay.engine import load_rules
from gametable.roleplay.store import Store
from gametable.story import StoryFlow, StoryFlowError
from gameclient.v1.clients.base import HostClientError
from gameclient.v1.host.manual import ManualControlError, ManualControlGate
from gameclient.v1.host.recorder import ManualInputRecorder
from organism.controllers.scripted_escort import ScriptedEscortController


class LeaseHandle:
    def __init__(self, owner):
        self.owner = owner
        self.released = False
    def release(self):
        self.released = True
        self.owner.released += 1
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.release()


class FakeLease:
    def __init__(self):
        self.acquired = []
        self.released = 0
    def acquire(self, owner_id, operation):
        self.acquired.append((owner_id, operation))
        return LeaseHandle(self)


class FakeEscort:
    def __init__(self, fail_first=False):
        self.fail_first = fail_first
        self.starts = 0
        self.cancelled = []
        self.paused = 0
    def start(self, *, day_id, offer_id):
        self.starts += 1
        if self.fail_first and self.starts == 1:
            from organism.controllers.scripted_escort import EscortError
            raise EscortError("temporary writer conflict")
        return {
            "job_id": "escort.test",
            "status": "escort_active",
            "phase": "following_leader",
            "controller_mode": "scripted_escort",
            "day_id": day_id,
            "offer_id": offer_id,
            "learned": False,
        }
    def cancel(self, reason="cancelled"):
        self.cancelled.append(reason)
        return {
            "job_id": "escort.test",
            "status": "cancelled",
            "phase": "released",
            "reason": reason,
        }
    def pause_for_restart(self):
        self.paused += 1
        return {
            "job_id": "escort.test",
            "status": "reconciling",
            "phase": "reconciling",
            "reason": "restart",
            "learned": False,
        }


class StoryFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "save.sqlite3", load_rules())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def flow(self, **kwargs):
        return StoryFlow(
            self.store,
            gate_path=self.root / "manual.json",
            intro_seconds=kwargs.pop("intro_seconds", 0.05),
            body_lease=kwargs.pop("body_lease", FakeLease()),
            **kwargs,
        )

    def publish_offer(self):
        story = self.store.story_state()
        story["intro"].update(
            offer_due=True,
            offer_published=True,
            phase="escort_offer_published",
            offer_text="Проводишь меня в лабораторию?",
        )
        self.store.set_story_state(
            story, expected_revision=story["story_revision"]
        )

    def test_default_manual_gate_is_inside_gametable_runtime(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("DIRECTOR_MANUAL_GATE", None)
            flow = StoryFlow(
                self.store,
                intro_seconds=0.05,
                body_lease=FakeLease(),
                escort=FakeEscort(),
            )
        expected = Path(__file__).resolve().parents[1] / "runtime" / "director-manual.json"
        self.assertEqual(flow.gate_path, expected)

    def test_intro_timer_counts_presence_once_and_pauses_without_ui(self):
        clock = {"now": 100.0}
        with patch(
            "gametable.story.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            flow = self.flow(intro_seconds=0.05, escort=FakeEscort())
            flow.note_director_message({
                "id": "turn-intro", "intent_id": "talk", "text": "Привет"
            })
            flow.presence_open("tab-1")
            flow.presence_open("tab-2")
            clock["now"] += 0.07
            flow._mark_offer_due_if_ready()
            story = self.store.story_state()
            self.assertTrue(story["intro"]["offer_due"])
            self.assertAlmostEqual(
                story["intro"]["elapsed_active_seconds"], 0.07, places=6
            )

            # A second scheduler check cannot mint a second offer.
            revision = story["story_revision"]
            flow._mark_offer_due_if_ready()
            self.assertEqual(self.store.story_state()["story_revision"], revision)
            flow.presence_close("tab-1")
            flow.presence_close("tab-2")

    def test_restart_does_not_count_downtime_as_active_intro(self):
        clock = {"now": 200.0}
        with patch(
            "gametable.story.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            flow = self.flow(intro_seconds=0.12, escort=FakeEscort())
            flow.note_director_message({
                "id": "turn-intro", "intent_id": "talk", "text": "Привет"
            })
            flow.presence_open("tab")
            clock["now"] += 0.04
            flow.presence_close("tab")
            before = self.store.story_state()["intro"]["elapsed_active_seconds"]
            clock["now"] += 0.12
            second = self.flow(intro_seconds=0.12, escort=FakeEscort())
            second._mark_offer_due_if_ready()
            after = self.store.story_state()["intro"]["elapsed_active_seconds"]
            self.assertAlmostEqual(after, before, places=6)
            self.assertAlmostEqual(after, 0.04, places=6)
            self.assertFalse(self.store.story_state()["intro"]["offer_due"])

    def test_explicit_accept_starts_scripted_escort_and_enables_gate(self):
        escort = FakeEscort()
        flow = self.flow(escort=escort)
        self.publish_offer()
        result = flow.respond(
            offer_id="escort-offer.day1",
            response="accept",
            text="Да, я провожу тебя.",
        )
        self.assertEqual(escort.starts, 1)
        self.assertEqual(result["escort"]["status"], "escort_active")
        gate = json.loads((self.root / "manual.json").read_text())
        self.assertTrue(gate["enabled"])
        self.assertEqual(gate["escort_id"], "escort.test")
        self.assertEqual(gate["day_id"], 1)
        self.assertFalse(result["escort"]["learned"])

    def test_gate_changes_only_after_story_update_commits(self):
        flow = self.flow(escort=FakeEscort())
        self.publish_offer()
        flow.respond(
            offer_id="escort-offer.day1",
            response="accept",
            text="Да.",
        )
        gate_path = self.root / "manual.json"
        self.assertTrue(json.loads(gate_path.read_text())["enabled"])
        active = self.store.story_state()
        blocked = {
            **active["escort"],
            "status": "blocked",
            "phase": "released",
            "reason": "simulated_conflict",
        }

        with patch.object(
            self.store,
            "set_story_state",
            side_effect=ValueError("story flow changed concurrently"),
        ):
            flow._on_escort_update(blocked, force=True)

        self.assertEqual(
            self.store.story_state()["escort"]["status"], "escort_active"
        )
        self.assertTrue(json.loads(gate_path.read_text())["enabled"])

        flow._on_escort_update(blocked, force=True)
        self.assertEqual(self.store.story_state()["escort"]["status"], "blocked")
        self.assertFalse(json.loads(gate_path.read_text())["enabled"])

    def test_stale_active_callback_cannot_reopen_terminal_escort_gate(self):
        flow = self.flow(escort=FakeEscort())
        active = {
            "job_id": "escort.race",
            "status": "escort_active",
            "phase": "following_leader",
            "last_tick": 10,
            "reason": None,
        }
        blocked = {
            **active,
            "status": "blocked",
            "phase": "released",
            "last_tick": 11,
            "reason": "escort_timeout",
        }
        flow._on_escort_update(active, force=True)
        flow._on_escort_update(blocked, force=True)
        flow._on_escort_update(active, force=True)

        story = self.store.story_state()
        self.assertEqual(story["escort"]["status"], "blocked")
        self.assertEqual(story["escort"]["last_tick"], 11)
        self.assertFalse(
            json.loads((self.root / "manual.json").read_text())["enabled"]
        )

    def test_director_host_rejection_is_a_story_error_not_http_thread_crash(self):
        flow = self.flow(escort=FakeEscort())

        class FailingDirector:
            def control_acquire(self, **_kwargs):
                raise HostClientError("manual Director input is locked until escort starts")

        with patch.object(flow, "_director_client", return_value=FailingDirector()):
            with self.assertRaisesRegex(
                StoryFlowError, "manual Director input is locked"
            ):
                flow.director_acquire("tab-a")

    def test_failed_accept_can_retry_same_offer_without_duplicate_dialogue(self):
        escort = FakeEscort(fail_first=True)
        flow = self.flow(escort=escort)
        self.publish_offer()
        with self.assertRaises(StoryFlowError):
            flow.respond(
                offer_id="escort-offer.day1",
                response="accept",
                text="Да.",
            )
        result = flow.respond(
            offer_id="escort-offer.day1",
            response="accept",
            text="Да.",
        )
        self.assertEqual(escort.starts, 2)
        messages = [
            row for row in self.store.dialogue()
            if row["message_id"] == "dialogue.escort-offer.day1.director.accept"
        ]
        self.assertEqual(len(messages), 1)
        self.assertEqual(result["escort"]["status"], "escort_active")

    def test_decline_never_starts_escort(self):
        escort = FakeEscort()
        flow = self.flow(escort=escort)
        self.publish_offer()
        result = flow.respond(
            offer_id="escort-offer.day1",
            response="decline",
            text="Не сейчас.",
        )
        self.assertEqual(escort.starts, 0)
        self.assertEqual(result["intro"]["phase"], "escort_declined")

    def test_clarify_keeps_offer_open_without_starting_escort(self):
        escort = FakeEscort()
        flow = self.flow(escort=escort)
        self.publish_offer()
        result = flow.respond(
            offer_id="escort-offer.day1",
            response="clarify",
            text="Куда именно?",
        )
        self.assertEqual(escort.starts, 0)
        self.assertIsNone(result["intro"]["response"])
        self.assertEqual(result["intro"]["phase"], "escort_offer_published")

    def test_sleep_schedules_one_idempotent_day_start_and_does_not_move_director(self):
        calls = []
        def world_rpc(_host, _port, payload, _timeout):
            calls.append(dict(payload))
            if payload["type"] == "day_start":
                return {"receipt": {
                    "action_id": "day.action.2",
                    "status": "queued",
                }}
            if payload["type"] == "receipt":
                return {"receipt": {
                    "action_id": "day.action.2",
                    "status": "applied",
                    "target_zone": "hallway",
                    "world_epoch": "epoch.1",
                    "tick": 90,
                    "observed_outcome": {
                        "day_start_id": "day-start.2",
                        "x": 0.0,
                        "learned_success": False,
                    },
                }}
            raise AssertionError(payload)
        escort = FakeEscort()
        flow = self.flow(
            escort=escort, world_rpc=world_rpc, body_lease=FakeLease()
        )
        first = flow.schedule_next_day()
        second = flow.schedule_next_day()
        self.assertEqual(first["day_start_id"], second["day_start_id"])
        flow._reconcile_day_start()
        story = self.store.story_state()
        self.assertEqual(story["day_id"], 2)
        self.assertEqual(story["day_phase"], "awake")
        self.assertEqual(story["day_start_placement"]["status"], "applied")
        day_calls = [row for row in calls if row["type"] == "day_start"]
        self.assertEqual(len(day_calls), 1)
        self.assertEqual(day_calls[0]["entity_id"], "entity.yuki")
        self.assertNotIn("entity.director", json.dumps(day_calls))

    def test_close_pauses_active_escort_for_reconciliation(self):
        escort = FakeEscort()
        flow = self.flow(escort=escort)
        self.publish_offer()
        flow.respond(
            offer_id="escort-offer.day1",
            response="accept",
            text="Да.",
        )
        flow.close()
        self.assertEqual(escort.paused, 1)
        self.assertEqual(
            self.store.story_state()["escort"]["status"], "reconciling"
        )


class ManualControlTests(unittest.TestCase):
    def test_gate_blocks_before_escort_and_fences_transfers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            path.write_text(json.dumps({"enabled": False}))
            gate = ManualControlGate(path)
            with self.assertRaises(ManualControlError):
                gate.acquire("tab-a")

            path.write_text(json.dumps({
                "enabled": True, "escort_id": "escort.1", "day_id": 1
            }))
            first = gate.acquire("tab-a")
            with self.assertRaises(ManualControlError):
                gate.acquire("tab-b")
            second = gate.acquire("tab-b", transfer=True)
            self.assertNotEqual(first["lease_id"], second["lease_id"])
            with self.assertRaises(ManualControlError):
                gate.validate("tab-a", first["lease_id"])
            self.assertEqual(
                gate.validate("tab-b", second["lease_id"])["day_id"], 1
            )

    def test_optional_recorder_is_bounded_and_never_marks_optimizer_enabled(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"DIRECTOR_ESCORT_RECORD": "1"}
        ):
            path = Path(directory) / "recording.json"
            recorder = ManualInputRecorder(path, limit=32)
            for index in range(40):
                recorder.record(
                    scope={"escort_id": "escort.1", "day_id": 1},
                    client_id="gui",
                    source="manual_host",
                    command={"move_x": 1, "motor_x": 1.0},
                    sequence=index + 1,
                    receipt={
                        "status": "applied", "world_epoch": "e", "tick": index
                    },
                    before={"x": index},
                    after={"x": index + 1},
                )
            recorder.close()
            payload = json.loads(path.read_text())
            self.assertEqual(len(payload["events"]), 32)
            self.assertFalse(payload["optimizer_enabled"])
            self.assertTrue(all(
                row["kind"] == "scripted_escort_demo"
                for row in payload["events"]
            ))


class ScriptedEscortTests(unittest.TestCase):
    def test_controller_uses_bounded_yuki_effort_and_physical_portal_arrival(self):
        snapshots = [
            {
                "world_epoch": "e1", "world_tick": 1,
                "entities": [
                    {"entity_id":"entity.yuki","zone_id":"hallway","x":0.0,"vx":0.0,
                     "last_sequence":0,"controller_id":"controller.yuki","controller_generation":1},
                    {"entity_id":"entity.director","zone_id":"hallway","x":1.0,"vx":0.0,
                     "last_sequence":0,"controller_id":"controller.director","controller_generation":1},
                ],
            },
            {
                "world_epoch": "e1", "world_tick": 2,
                "entities": [
                    {"entity_id":"entity.yuki","zone_id":"hallway","x":5.0,"vx":5.0,
                     "last_sequence":0,"controller_id":"controller.yuki","controller_generation":1},
                    {"entity_id":"entity.director","zone_id":"hallway","x":40.0,"vx":5.0,
                     "last_sequence":0,"controller_id":"controller.director","controller_generation":1},
                ],
            },
            {
                "world_epoch": "e1", "world_tick": 3,
                "entities": [
                    {"entity_id":"entity.yuki","zone_id":"hallway","x":498.0,"vx":10.0,
                     "last_sequence":1,"controller_id":"controller.yuki","controller_generation":1},
                    {"entity_id":"entity.director","zone_id":"laboratory","x":1.0,"vx":0.0,
                     "last_sequence":2,"controller_id":"controller.director","controller_generation":2},
                ],
            },
            {
                "world_epoch": "e1", "world_tick": 4,
                "entities": [
                    {"entity_id":"entity.yuki","zone_id":"laboratory","x":1.0,"vx":0.0,
                     "last_sequence":2,"controller_id":"controller.yuki","controller_generation":2},
                    {"entity_id":"entity.director","zone_id":"laboratory","x":12.0,"vx":0.0,
                     "last_sequence":2,"controller_id":"controller.director","controller_generation":2},
                ],
            },
        ]
        calls = []
        index = {"value": 0}
        def world_rpc(_host, _port, payload, _timeout):
            calls.append(dict(payload))
            if payload["type"] == "snapshot":
                value = snapshots[min(index["value"], len(snapshots)-1)]
                index["value"] += 1
                return {"snapshot": value}
            if payload["type"] == "input":
                return {"receipt": {
                    "status": "applied",
                    "action_id": "input.test",
                    "reason_code": "ok",
                }}
            raise AssertionError(payload)

        lease = FakeLease()
        controller = ScriptedEscortController(
            world_rpc=world_rpc,
            body_lease=lease,
            hz=1000,
            timeout_seconds=1.0,
        )
        started = controller.start(day_id=1, offer_id="escort-offer.day1")
        self.assertEqual(started["controller_mode"], "scripted_escort")
        deadline = time.time() + 1
        while controller.status()["status"] not in {
            "scripted_arrival", "blocked", "failed"
        }:
            self.assertLess(time.time(), deadline)
            time.sleep(0.005)
        final = controller.status()
        self.assertEqual(final["status"], "scripted_arrival", final)
        self.assertFalse(final["learned"])
        inputs = [row for row in calls if row["type"] == "input"]
        self.assertTrue(inputs)
        self.assertTrue(all(row["entity_id"] == "entity.yuki" for row in inputs))
        self.assertTrue(all(abs(row["motor_x"]) <= 0.58 for row in inputs))
        self.assertFalse(any(row["type"] in {"transfer","setup_reset","day_start"}
                             for row in calls))


if __name__ == "__main__":
    unittest.main()
