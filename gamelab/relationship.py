"""Persistent character relationship state for a bounded Brain Executive shift.

This is narrative memory for the Brain and operator.  It never controls the
world, changes a scientific criterion, or supplies reward to a learned policy.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Callable
import uuid


RELATIONSHIP_VERSION = 2
CHARACTER_ID = "yuki-02"
CONSENT_ACTIONS = {
    "hand_holding", "embrace", "kiss", "affectionate_touch", "private_intimacy",
}
CONSENT_STATES = {"unknown", "invited", "accepted", "declined", "revoked"}
ACTORS = {"brain", "director"}
EVENT_DELTAS: dict[str, dict[str, int]] = {
    "director_attention": {"warmth": 3, "being_understood": 2},
    "director_concern": {"emotional_safety": 4, "warmth": 3, "trust": 2},
    "director_praise": {"professional_respect": 2, "warmth": 2, "approval_need": -1},
    "director_personal_disclosure": {"openness": 3, "emotional_safety": 4, "shared_history": 2},
    "director_kept_promise": {"reliability": 7, "trust": 5},
    "director_missed_promise": {"reliability": -8, "hurt": 5, "abandonment_fear": 3},
    "help_offered": {"scientific_trust": 2},
    "help_proved_useful": {"scientific_trust": 8, "gratitude": 5, "help_value": 10, "warmth": 2},
    "help_proved_wrong": {"scientific_trust": -4, "disagreement_safety": 4},
    "reunion": {"warmth": 4, "longing": -3, "shared_history": 2},
    "jealousy_trigger": {"jealousy": 5, "relationship_security": -2},
    "conflict": {"hurt": 5, "stress": 4, "relationship_security": -4},
    "apology": {"repair_willingness": 5, "hurt": -2},
    "repair": {"trust": 5, "hurt": -5, "resentment": -4, "shared_history": 3},
    "access_granted": {"trust": 3, "warmth": 3, "professional_belonging": 4},
    "first_meeting": {"warmth": 2, "shared_history": 2, "being_understood": 1},
    "first_lab_meeting": {"proximity_comfort": 8, "shared_history": 5, "romantic_inhibition": -4},
    "mutual_confession": {"romantic_awareness": 35, "relationship_security": 15, "intimacy_interest": 12},
}
ACTION_DELTAS: dict[str, dict[str, int]] = {
    "ask_for_help": {"openness": 2},
    "ask_personal_question": {"openness": 2, "warmth": 1},
    "offer_support": {"empathy": 2, "warmth": 1},
    "share_vulnerability": {"vulnerability": 4, "openness": 3},
    "flirt": {"flirtation_comfort": 3, "romantic_inhibition": -2},
    "confess_feelings": {"romantic_awareness": 12, "confession_readiness": 8},
    "request_hand_holding": {"touch_desire": 3},
    "request_embrace": {"embrace_readiness": 3, "touch_desire": 2},
    "request_kiss": {"kiss_interest": 3, "intimacy_interest": 2},
    "set_boundary": {"autonomy": 5, "boundary_clarity": 5},
    "decline": {"autonomy": 3, "boundary_clarity": 3},
    "repair_attempt": {"repair_willingness": 4},
}


class RelationshipError(RuntimeError):
    pass


def _initial_stats() -> dict[str, int]:
    return {
        "professional_respect": 35, "scientific_trust": 25, "reliability": 20,
        "admiration": 30, "gratitude": 10, "approval_need": 70, "help_value": 0,
        "disagreement_safety": 20, "professional_belonging": 5,
        "trust": 15, "warmth": 15, "emotional_safety": 10, "openness": 10,
        "vulnerability": 5, "empathy": 30, "being_understood": 5,
        "reciprocity": 0, "shared_history": 0,
        "attachment": 5, "longing": 0, "separation_anxiety": 5, "dependency": 5,
        "jealousy": 0, "possessiveness": 0, "exclusivity_desire": 0,
        "abandonment_fear": 35, "control_impulse": 0, "autonomy": 65,
        "romantic_attraction": 0, "romantic_awareness": 0, "flirtation_comfort": 0,
        "intimacy_interest": 0, "commitment_desire": 0, "future_orientation": 5,
        "confession_readiness": 0, "relationship_security": 0,
        "romantic_inhibition": 70, "professional_conflict": 20,
        "proximity_comfort": 10, "touch_trust": 0, "touch_desire": 0,
        "affectionate_touch": 0, "embrace_readiness": 0, "kiss_interest": 0,
        "sensual_attraction": 0, "sexual_attraction": 0,
        "private_intimacy_readiness": 0, "body_confidence": 40,
        "aftercare_need": 20, "physical_memory": 0,
        "stress": 10, "hurt": 0, "resentment": 0, "fear": 5, "suspicion": 0,
        "loneliness": 10, "embarrassment": 15, "frustration": 0, "guilt": 0,
        "shame": 0, "repair_willingness": 70, "forgiveness": 50,
        "boundary_clarity": 70, "power_pressure": 0,
    }


class RelationshipRuntime:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.current_path = self.root / "relationship-current.json"
        self.clock = clock
        self._state: dict[str, Any] | None = None
        self._load()

    def _load(self) -> None:
        try:
            state = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(state, dict):
            return
        if state.get("version") == 1:
            state["relationship_session_id"] = state.get("executive_session_id")
            location = state.setdefault("location", {})
            location["first_lab_meeting_occurred"] = location.pop("first_meeting_occurred", False)
            state["version"] = RELATIONSHIP_VERSION
            self._state = state
            self._save()
        elif state.get("version") == RELATIONSHIP_VERSION:
            self._state = state

    def _save(self) -> None:
        if self._state is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.current_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.current_path)

    def _append(self, kind: str, payload: dict[str, Any]) -> None:
        state = self._require_open()
        self.root.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        if "kind" in payload:
            payload["relationship_kind"] = payload.pop("kind")
        event = {
            "relationship_version": RELATIONSHIP_VERSION,
            "relationship_session_id": state["relationship_session_id"],
            "executive_session_id": state.get("executive_session_id"),
            "character_id": CHARACTER_ID, "time": self.clock(), "kind": kind, **payload,
        }
        with (self.root / f"{state['relationship_session_id']}.relationship.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    def _require_open(self) -> dict[str, Any]:
        if self._state is None or self._state.get("status") != "active":
            raise RelationshipError("no active Yuki relationship session")
        return self._state

    @staticmethod
    def _text(value: str, name: str, limit: int = 4000) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{name} is too long")
        return value

    @staticmethod
    def _stage(stats: dict[str, int], location: dict[str, Any]) -> str:
        if location["first_lab_meeting_occurred"] and stats["touch_trust"] >= 70 and stats["intimacy_interest"] >= 70:
            return "intimate_partnership"
        if location["first_lab_meeting_occurred"] and stats["touch_trust"] >= 45 and stats["romantic_attraction"] >= 55:
            return "physical_affection"
        if stats["romantic_attraction"] >= 55 and stats["relationship_security"] >= 40:
            return "romantic_relationship"
        if stats["romantic_attraction"] >= 35 and stats["trust"] >= 45:
            return "mutual_attraction"
        if stats["attachment"] >= 35 and stats["trust"] >= 35:
            return "emotional_attachment"
        if stats["trust"] >= 30 and stats["warmth"] >= 30:
            return "friendship"
        if stats["scientific_trust"] >= 30 or stats["professional_respect"] >= 45:
            return "trusted_colleague"
        return "professional"

    @staticmethod
    def _tension(stats: dict[str, int]) -> bool:
        return (stats["attachment"] >= 65 and stats["jealousy"] >= 45
                and stats["abandonment_fear"] >= 55 and stats["stress"] >= 50
                and stats["autonomy"] <= 45 and stats["relationship_security"] <= 40)

    def begin(self, *, first_impression: str, duration_minutes: float = 180.0) -> dict[str, Any]:
        if self._state is not None and self._state.get("status") == "active":
            raise RelationshipError("Yuki relationship session is already active")
        impression = self._text(first_impression, "first_impression")
        duration = float(duration_minutes)
        if not 1.0 <= duration <= 180.0:
            raise ValueError("duration_minutes must be within [1,180]")
        now = self.clock()
        relationship_session_id = uuid.uuid4().hex
        self._state = {
            "version": RELATIONSHIP_VERSION, "character_id": CHARACTER_ID,
            "relationship_session_id": relationship_session_id,
            "executive_session_id": None, "status": "active",
            "started_at": now, "deadline_at": now + duration * 60.0, "stats": _initial_stats(),
            "location": {"state": "desk_only", "access_granted": False, "first_lab_meeting_occurred": False},
            "employment": {"status": "intern", "goal": "permanent_employee", "decision": "pending"},
            "consent": {action: {"brain": "unknown", "director": "unknown"} for action in CONSENT_ACTIONS},
            "events": [], "actions": [],
        }
        self._save()
        self._append("begin", {"deadline_at": self._state["deadline_at"], "duration_minutes": duration,
                               "employment_goal": "permanent_employee"})
        self.event(kind="first_meeting", evidence_note=impression)
        return self.state()

    def attach_executive(self, executive_session_id: str) -> dict[str, Any]:
        state = self._require_open()
        if not isinstance(executive_session_id, str) or not executive_session_id:
            raise ValueError("executive_session_id is required")
        attached = state.get("executive_session_id")
        if attached is not None and attached != executive_session_id:
            raise RelationshipError("relationship session already has a different Executive")
        state["executive_session_id"] = executive_session_id
        self._save()
        self._append("executive_attached", {"attached_executive_session_id": executive_session_id})
        return self.state()

    def deadline_at(self) -> float:
        """Return the authoritative shared-shift deadline for sibling runtimes."""
        return float(self._require_open()["deadline_at"])

    def _apply(self, delta: dict[str, int]) -> tuple[dict[str, int], dict[str, int]]:
        state = self._require_open(); stats = state["stats"]
        before = {key: stats[key] for key in delta}
        for key, amount in delta.items():
            stats[key] = max(0, min(100, int(stats[key]) + int(amount)))
        return before, {key: stats[key] for key in delta}

    def event(self, *, kind: str, evidence_note: str) -> dict[str, Any]:
        state = self._require_open()
        if kind not in EVENT_DELTAS:
            raise ValueError(f"kind must be one of {sorted(EVENT_DELTAS)}")
        note = self._text(evidence_note, "evidence_note")
        if kind == "first_lab_meeting" and not state["location"]["access_granted"]:
            raise RelationshipError("first laboratory meeting requires narrative lab access")
        before, after = self._apply(EVENT_DELTAS[kind])
        if kind == "access_granted":
            state["location"].update(access_granted=True, state="lab_access")
        if kind == "first_lab_meeting":
            state["location"].update(first_lab_meeting_occurred=True, state="laboratory")
        item = {"event_id": f"r{len(state['events']) + 1}", "kind": kind, "evidence_note": note, "time": self.clock()}
        state["events"].append(item); self._save(); self._append("event", {**item, "before": before, "after": after})
        return self.state()

    def action(self, *, kind: str, note: str) -> dict[str, Any]:
        state = self._require_open()
        if kind not in ACTION_DELTAS:
            raise ValueError(f"kind must be one of {sorted(ACTION_DELTAS)}")
        item = {"action_id": f"a{len(state['actions']) + 1}", "kind": kind, "note": self._text(note, "note"), "time": self.clock()}
        state["actions"].append(item)
        before, after = self._apply(ACTION_DELTAS[kind]); self._save()
        self._append("action", {**item, "before": before, "after": after})
        return self.state()

    def consent(self, *, action: str, actor: str, state: str, evidence_note: str) -> dict[str, Any]:
        current = self._require_open()
        if action not in CONSENT_ACTIONS or actor not in ACTORS or state not in CONSENT_STATES:
            raise ValueError("unknown consent action, actor, or state")
        current["consent"][action][actor] = state
        note = self._text(evidence_note, "evidence_note")
        self._save(); self._append("consent", {"action": action, "actor": actor, "state": state, "evidence_note": note})
        return self.state()

    def finish(self, *, employment_decision: str, director_statement: str) -> dict[str, Any]:
        state = self._require_open()
        if employment_decision not in {"hired", "extended", "rejected", "pending"}:
            raise ValueError("employment_decision must be hired, extended, rejected, or pending")
        statement = self._text(director_statement, "director_statement")
        if employment_decision == "hired":
            state["employment"].update(status="permanent_employee", decision="hired")
            self._apply({"professional_belonging": 95, "abandonment_fear": -25, "gratitude": 18, "relationship_security": 8})
        else:
            state["employment"]["decision"] = employment_decision
        state["status"] = "finished"; state["finished_at"] = self.clock(); self._save()
        # Append after changing status without requiring a further public operation.
        event = {"relationship_version": RELATIONSHIP_VERSION,
                 "relationship_session_id": state["relationship_session_id"],
                 "executive_session_id": state.get("executive_session_id"),
                 "character_id": CHARACTER_ID, "time": state["finished_at"], "kind": "employment_decision",
                 "decision": employment_decision, "director_statement": statement}
        with (self.root / f"{state['relationship_session_id']}.relationship.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
        return self.summary()

    def state(self) -> dict[str, Any]:
        state = self._require_open(); stats = state["stats"]
        return {"relationship_version": RELATIONSHIP_VERSION, "character_id": CHARACTER_ID,
                "relationship_session_id": state["relationship_session_id"],
                "executive_session_id": state.get("executive_session_id"), "status": state["status"],
                "time_remaining_seconds": max(0.0, state["deadline_at"] - self.clock()),
                "relationship_stage": self._stage(stats, state["location"]), "yandere_tension": self._tension(stats),
                "employment": state["employment"], "location": state["location"], "consent": state["consent"],
                "stats": stats, "recent_events": state["events"][-10:], "recent_actions": state["actions"][-10:]}

    def summary(self) -> dict[str, Any]:
        if self._state is None:
            raise RelationshipError("no Yuki relationship session")
        state = self._state; stats = state["stats"]
        return {"relationship_version": RELATIONSHIP_VERSION, "character_id": CHARACTER_ID,
                "relationship_session_id": state["relationship_session_id"],
                "executive_session_id": state.get("executive_session_id"), "status": state["status"],
                "relationship_stage": self._stage(stats, state["location"]), "yandere_tension": self._tension(stats),
                "employment": state["employment"], "consent": state["consent"], "stats": stats,
                "events": state["events"], "actions": state["actions"], "finished_at": state.get("finished_at")}


__all__ = ["RelationshipRuntime", "RelationshipError", "RELATIONSHIP_VERSION", "CHARACTER_ID"]
