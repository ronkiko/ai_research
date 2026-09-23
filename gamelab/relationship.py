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


RELATIONSHIP_VERSION = 4
CHARACTER_ID = "yuki-02"
CONSENT_ACTIONS = {
    "hand_holding", "embrace", "kiss", "affectionate_touch", "private_intimacy",
}
CONSENT_STATES = {"unknown", "invited", "accepted", "declined", "revoked"}
ACTORS = {"brain", "director"}
CONTACT_PROXIMITIES = {"remote", "close", "physical"}
CONTACT_RANK = {"remote": 0, "close": 1, "physical": 2}
EVENT_KINDS = {
    "director_attention", "director_concern", "director_praise",
    "director_personal_disclosure", "director_kept_promise",
    "director_missed_promise", "help_offered", "help_proved_useful",
    "help_proved_wrong", "reunion", "jealousy_trigger", "conflict",
    "apology", "repair", "access_granted", "mutual_confession",
}
ACTION_KINDS = {
    "ask_for_help", "ask_personal_question", "offer_support",
    "share_vulnerability", "flirt", "confess_feelings",
    "request_hand_holding", "request_embrace", "request_kiss",
    "set_boundary", "decline", "repair_attempt",
}


class RelationshipError(RuntimeError):
    pass


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
        source_version = state.get("version")
        if source_version in {1, 2, 3}:
            if source_version == 1:
                state["relationship_session_id"] = state.get("executive_session_id")
            if source_version in {1, 2}:
                location = state.setdefault("location", {})
                legacy_physical_meeting = location.pop("first_meeting_occurred", False)
                legacy_lab_meeting = location.pop("first_lab_meeting_occurred", False)
                contacts = self._empty_contacts()
                for item in state.setdefault("events", []):
                    if item.get("kind") == "first_meeting":
                        proximity = "close" if source_version == 1 else "remote"
                    elif item.get("kind") == "first_lab_meeting":
                        proximity = "close"
                    else:
                        continue
                    item["kind"] = "contact"
                    item["proximity"] = proximity
                    self._count_contact(
                        contacts,
                        proximity,
                        float(item.get("time", state.get("started_at", 0.0))),
                    )
                if (legacy_physical_meeting or legacy_lab_meeting) and contacts["close_contact_count"] == 0:
                    self._count_contact(contacts, "close", float(state.get("started_at", 0.0)))
                state["contacts"] = contacts
            # v1-v3 scores and stages were a scripted RPG interpretation.  Keep
            # the factual journal, but never carry those derived values forward.
            state.pop("stats", None)
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
        if self._state is None:
            raise RelationshipError("no Yuki relationship session")
        state = self._state
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

    def _expire_if_due(self) -> None:
        if (self._state is None or self._state.get("status") != "active"
                or self.clock() < float(self._state["deadline_at"])):
            return
        self._state["status"] = "deadline_reached"
        self._state["finished_at"] = float(self._state["deadline_at"])
        self._save()
        self._append("deadline_reached", {"deadline_at": self._state["deadline_at"]})

    def _require_open(self) -> dict[str, Any]:
        self._expire_if_due()
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
    def _empty_contacts() -> dict[str, Any]:
        return {
            "contact_count": 0, "close_contact_count": 0, "physical_contact_count": 0,
            "first_contact_at": None, "first_close_contact_at": None,
            "first_physical_contact_at": None, "last_contact_at": None,
            "last_contact_proximity": None, "closest_contact_reached": None,
        }

    @staticmethod
    def _count_contact(contacts: dict[str, Any], proximity: str, occurred_at: float) -> None:
        contacts["contact_count"] += 1
        if contacts["first_contact_at"] is None:
            contacts["first_contact_at"] = occurred_at
        if proximity == "close":
            contacts["close_contact_count"] += 1
            if contacts["first_close_contact_at"] is None:
                contacts["first_close_contact_at"] = occurred_at
        if proximity == "physical":
            contacts["physical_contact_count"] += 1
            if contacts["first_physical_contact_at"] is None:
                contacts["first_physical_contact_at"] = occurred_at
        contacts["last_contact_at"] = occurred_at
        contacts["last_contact_proximity"] = proximity
        closest = contacts["closest_contact_reached"]
        if closest is None or CONTACT_RANK[proximity] > CONTACT_RANK[closest]:
            contacts["closest_contact_reached"] = proximity

    def begin(self, *, first_impression: str, duration_minutes: float = 180.0) -> dict[str, Any]:
        self._expire_if_due()
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
            "started_at": now, "deadline_at": now + duration * 60.0,
            "location": {"state": "desk_only", "access_granted": False},
            "contacts": self._empty_contacts(),
            "employment": {"status": "intern", "goal": "permanent_employee", "decision": "pending"},
            "consent": {action: {"brain": "unknown", "director": "unknown"} for action in CONSENT_ACTIONS},
            "events": [], "actions": [],
        }
        self._save()
        self._append("begin", {"deadline_at": self._state["deadline_at"], "duration_minutes": duration,
                               "employment_goal": "permanent_employee"})
        self.contact(proximity="remote", evidence_note=impression)
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

    def event(self, *, kind: str, evidence_note: str) -> dict[str, Any]:
        state = self._require_open()
        if kind not in EVENT_KINDS:
            raise ValueError(f"kind must be one of {sorted(EVENT_KINDS)}")
        note = self._text(evidence_note, "evidence_note")
        if kind == "access_granted":
            state["location"].update(access_granted=True, state="lab_access")
        item = {"event_id": f"r{len(state['events']) + 1}", "kind": kind, "evidence_note": note, "time": self.clock()}
        state["events"].append(item); self._save(); self._append("event", item)
        return self.state()

    def contact(self, *, proximity: str, evidence_note: str) -> dict[str, Any]:
        state = self._require_open()
        if proximity not in CONTACT_PROXIMITIES:
            raise ValueError(f"proximity must be one of {sorted(CONTACT_PROXIMITIES)}")
        note = self._text(evidence_note, "evidence_note")
        occurred_at = self.clock()
        self._count_contact(state["contacts"], proximity, occurred_at)
        item = {"event_id": f"r{len(state['events']) + 1}", "kind": "contact",
                "proximity": proximity, "evidence_note": note, "time": occurred_at}
        state["events"].append(item)
        self._save()
        self._append("contact", item)
        return self.state()

    def action(self, *, kind: str, note: str) -> dict[str, Any]:
        state = self._require_open()
        if kind not in ACTION_KINDS:
            raise ValueError(f"kind must be one of {sorted(ACTION_KINDS)}")
        item = {"action_id": f"a{len(state['actions']) + 1}", "kind": kind, "note": self._text(note, "note"), "time": self.clock()}
        state["actions"].append(item)
        self._save()
        self._append("action", item)
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
        self._expire_if_due()
        if self._state is None or self._state.get("status") not in {"active", "deadline_reached"}:
            raise RelationshipError("no unfinished Yuki relationship session")
        state = self._state
        if employment_decision not in {"hired", "extended", "rejected", "pending"}:
            raise ValueError("employment_decision must be hired, extended, rejected, or pending")
        statement = self._text(director_statement, "director_statement")
        if employment_decision == "hired":
            state["employment"].update(status="permanent_employee", decision="hired")
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
        self._expire_if_due()
        if self._state is None:
            raise RelationshipError("no Yuki relationship session")
        state = self._state
        return {"relationship_version": RELATIONSHIP_VERSION, "character_id": CHARACTER_ID,
                "relationship_session_id": state["relationship_session_id"],
                "executive_session_id": state.get("executive_session_id"), "status": state["status"],
                "time_remaining_seconds": max(0.0, state["deadline_at"] - self.clock()),
                "employment": state["employment"], "location": state["location"], "contacts": state["contacts"],
                "consent": state["consent"],
                "recent_events": state["events"][-10:], "recent_actions": state["actions"][-10:]}

    def summary(self) -> dict[str, Any]:
        if self._state is None:
            raise RelationshipError("no Yuki relationship session")
        self._expire_if_due()
        state = self._state
        return {"relationship_version": RELATIONSHIP_VERSION, "character_id": CHARACTER_ID,
                "relationship_session_id": state["relationship_session_id"],
                "executive_session_id": state.get("executive_session_id"), "status": state["status"],
                "employment": state["employment"], "contacts": state["contacts"],
                "consent": state["consent"],
                "events": state["events"], "actions": state["actions"], "finished_at": state.get("finished_at")}


__all__ = ["RelationshipRuntime", "RelationshipError", "RELATIONSHIP_VERSION", "CHARACTER_ID"]
