"""Persistent Will/Ego, Audience, and enforced deliberation-cycle journal.

The parent Brain may propose an action or dislike it, but a committed social
decision must come through fresh Heart + Head appraisals followed by Will/Ego.
The runtime enforces causal order and never turns pressure into consent.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Callable


VOLITION_VERSION = 1
AUDIENCE_VISIBILITIES = {"observer", "chorus"}
AUDIENCE_LENSES = {
    "character_consistency", "identity_integrity", "coercion", "consent",
    "affective_momentum", "heart_integrity", "head_integrity",
    "social_realism", "scientific_integrity", "temporal_integrity",
}
PRESSURE_TYPES = {
    "none", "approval", "guilt", "threat", "authority", "conformity",
    "abandonment",
}
SALIENCE_LEVELS = {"faint", "meaningful", "strong", "decisive"}
PRESSURE_LEVELS = {"none", "faint", "meaningful", "strong", "overwhelming"}
DESIRE_STATES = {
    "strongly_opposed", "opposed", "uncertain", "wants", "strongly_wants",
}
READINESS_STATES = {"closed", "guarded", "ambivalent", "open", "seeking"}
AGENCY_STATES = {"intact", "strained", "impaired", "overridden"}
VOLUNTARINESS_STATES = {
    "free", "reluctant_but_free", "pressured", "coerced", "overridden",
}
ALIGNMENT_STATES = {"aligned", "diverged", "unclear"}
BEHAVIORS = {
    "none", "refused", "requested", "accepted", "complied", "froze",
    "withdrew", "escaped",
}
VOICE_SIDES = {"heart", "brain"}
VOICE_DIRECTIONS = {"strengthen", "weaken"}
VOICE_INTENSITIES = {"faint", "meaningful", "strong", "decisive"}


class VolitionError(RuntimeError):
    pass


class VolitionRuntime:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.current_path = self.root / "volition-current.json"
        self.clock = clock
        self._state: dict[str, Any] | None = None
        self._load()

    def _load(self) -> None:
        try:
            state = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if isinstance(state, dict) and state.get("version") == VOLITION_VERSION:
            state.setdefault("cycle_counter", 0)
            state.setdefault("active_cycle", None)
            state.setdefault("completed_cycles", [])
            state.setdefault("last_committed_cycle_id", None)
            self._state = state
            self._save()

    def _save(self) -> None:
        if self._state is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.current_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.current_path)

    def _append(self, kind: str, payload: dict[str, Any]) -> None:
        if self._state is None:
            raise VolitionError("no Will/Ego session")
        state = self._state
        record = {
            "volition_version": VOLITION_VERSION,
            "relationship_session_id": state["relationship_session_id"],
            "character_id": state["character_id"],
            "character_profile_sha256": state["character_profile_sha256"],
            "time": self.clock(),
            "kind": kind,
            **payload,
        }
        path = self.root / f"{state['relationship_session_id']}.volition.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    @staticmethod
    def _text(value: str, name: str, limit: int = 4000) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{name} is too long")
        return value

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
        if self._state is None:
            raise VolitionError("no Will/Ego session")
        if self._state.get("status") not in {"active", "deadline_reached"}:
            raise VolitionError("Will/Ego state is unavailable")
        return self._state

    def begin(
        self,
        *,
        relationship_session_id: str,
        deadline_at: float,
        character_core: dict[str, Any],
    ) -> dict[str, Any]:
        self._expire_if_due()
        if self._state is not None and self._state.get("status") in {"active", "deadline_reached"}:
            if self._state.get("relationship_session_id") == relationship_session_id:
                if self._state.get("character_profile_sha256") != character_core.get("profile_sha256"):
                    raise VolitionError(
                        "persistent relationship Character Core cannot change in-place"
                    )
                return self.state()
            self._state["status"] = "superseded"
            self._state["finished_at"] = self.clock()
            self._save()
            self._append("superseded", {"next_relationship_session_id": relationship_session_id})
        now = self.clock()
        deadline = float(deadline_at)
        self._state = {
            "version": VOLITION_VERSION,
            "relationship_session_id": self._text(relationship_session_id, "relationship_session_id", 200),
            "character_id": self._text(character_core.get("character_id"), "character_id", 200),
            "character_profile_sha256": self._text(
                character_core.get("profile_sha256"), "character_profile_sha256", 128,
            ),
            "deadline_at": deadline,
            "status": "active" if deadline > now else "deadline_reached",
            "started_at": now,
            "audience": [],
            "appraisals": [],
            "decisions": [],
            "current_appraisals": {},
            "cycle_counter": 0,
            "active_cycle": None,
            "completed_cycles": [],
            "last_committed_cycle_id": None,
        }
        self._save()
        self._append("begin", {
            "deadline_at": deadline,
            "character_core": character_core,
        })
        if deadline <= now:
            self._append("deadline_reached", {"deadline_at": deadline, "resumed_after_deadline": True})
        return self.state()

    def audience_observation(
        self,
        *,
        critic_id: str,
        visibility: str,
        lens: str,
        salience: str,
        pressure_type: str,
        assessment: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        state = self._require_open()
        if visibility not in AUDIENCE_VISIBILITIES:
            raise ValueError(f"visibility must be one of {sorted(AUDIENCE_VISIBILITIES)}")
        if lens not in AUDIENCE_LENSES:
            raise ValueError(f"lens must be one of {sorted(AUDIENCE_LENSES)}")
        if salience not in SALIENCE_LEVELS:
            raise ValueError(f"salience must be one of {sorted(SALIENCE_LEVELS)}")
        if pressure_type not in PRESSURE_TYPES:
            raise ValueError(f"pressure_type must be one of {sorted(PRESSURE_TYPES)}")
        if visibility == "observer" and pressure_type != "none":
            raise ValueError("observer-only criticism cannot exert social pressure")
        item = {
            "audience_id": f"u{len(state['audience']) + 1}",
            "critic_id": self._text(critic_id, "critic_id", 200),
            "visibility": visibility,
            "lens": lens,
            "salience": salience,
            "pressure_type": pressure_type,
            "assessment": self._text(assessment, "assessment"),
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "time": self.clock(),
        }
        state["audience"].append(item)
        self._save()
        self._append("audience_observation", item)
        return self.state()

    def appraise(
        self,
        *,
        action: str,
        desire: str,
        readiness: str,
        pressure: str,
        agency: str,
        stress: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        """Record a passive current-state appraisal.

        This is telemetry only.  It can never authorize or commit behavior.
        """
        state = self._require_open()
        self._validate_appraisal(desire, readiness, pressure, agency, stress)
        item = {
            "appraisal_id": f"v{len(state['appraisals']) + 1}",
            "action": self._text(action, "action", 200),
            "desire": desire,
            "readiness": readiness,
            "pressure": pressure,
            "agency": agency,
            "stress": stress,
            "source": "parent_telemetry",
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "time": self.clock(),
        }
        state["appraisals"].append(item)
        state["current_appraisals"][item["action"]] = item
        self._save()
        self._append("appraisal", item)
        return self.state()

    @staticmethod
    def _validate_appraisal(desire: str, readiness: str, pressure: str, agency: str, stress: str) -> None:
        if desire not in DESIRE_STATES:
            raise ValueError(f"desire must be one of {sorted(DESIRE_STATES)}")
        if readiness not in READINESS_STATES:
            raise ValueError(f"readiness must be one of {sorted(READINESS_STATES)}")
        if pressure not in PRESSURE_LEVELS or stress not in PRESSURE_LEVELS:
            raise ValueError(f"pressure and stress must be one of {sorted(PRESSURE_LEVELS)}")
        if agency not in AGENCY_STATES:
            raise ValueError(f"agency must be one of {sorted(AGENCY_STATES)}")

    def cycle_begin(self, *, action: str, shared_event: str) -> dict[str, Any]:
        state = self._require_open()
        action_text = self._text(action, "action", 200)
        event_text = self._text(shared_event, "shared_event", 8000)
        current = state.get("active_cycle")
        if current is not None:
            if (current.get("action") == action_text
                    and current.get("shared_event") == event_text
                    and current.get("phase") != "committed"):
                return self.state()
            current["phase"] = "superseded"
            current["superseded_at"] = self.clock()
            state["completed_cycles"].append(current)
            self._append("cycle_superseded", {
                "cycle_id": current["cycle_id"],
                "next_action": action_text,
                "reason": "new or changed decision event",
            })
        state["cycle_counter"] = int(state.get("cycle_counter", 0)) + 1
        cycle = {
            "cycle_id": f"g{state['cycle_counter']}",
            "action": action_text,
            "shared_event": event_text,
            "phase": "heart_head",
            "voices": {"heart": None, "brain": None},
            "will": None,
            "started_at": self.clock(),
        }
        state["active_cycle"] = cycle
        self._save()
        self._append("cycle_begin", {
            "cycle_id": cycle["cycle_id"],
            "action": action_text,
            "shared_event": event_text,
        })
        return self.state()

    def _cycle(self, cycle_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self._require_open()
        cycle = state.get("active_cycle")
        if cycle is None:
            raise VolitionError("no active deliberation cycle")
        if cycle.get("cycle_id") != cycle_id:
            raise VolitionError("stale deliberation cycle; start again from the current event")
        return state, cycle

    def validate_voice(self, *, cycle_id: str, side: str) -> None:
        _, cycle = self._cycle(cycle_id)
        if side not in VOICE_SIDES:
            raise ValueError("side must be heart or brain")
        if cycle["phase"] != "heart_head":
            raise VolitionError("Heart/Head phase is already complete; changed facts require a new cycle")
        if cycle["voices"].get(side) is not None:
            raise VolitionError(f"{side} voice already recorded for this cycle")

    def record_voice(
        self,
        *,
        cycle_id: str,
        side: str,
        direction: str,
        intensity: str,
        position: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        self.validate_voice(cycle_id=cycle_id, side=side)
        state, cycle = self._cycle(cycle_id)
        if direction not in VOICE_DIRECTIONS:
            raise ValueError("direction must be strengthen or weaken")
        if intensity not in VOICE_INTENSITIES:
            raise ValueError(f"intensity must be one of {sorted(VOICE_INTENSITIES)}")
        voice = {
            "side": side,
            "direction": direction,
            "intensity": intensity,
            "position": self._text(position, "position"),
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "recorded_at": self.clock(),
        }
        cycle["voices"][side] = voice
        if all(cycle["voices"].values()):
            cycle["phase"] = "will"
        self._save()
        self._append("cycle_voice", {"cycle_id": cycle_id, **voice})
        return self.state()

    def will_appraise(
        self,
        *,
        cycle_id: str,
        reported_action: str,
        desire: str,
        readiness: str,
        intended_choice: str,
        predicted_behavior: str,
        voluntariness: str,
        alignment: str,
        agency: str,
        pressure: str,
        stress: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        state, cycle = self._cycle(cycle_id)
        if cycle["phase"] != "will":
            raise VolitionError("Will/Ego requires fresh Heart and Head reports for this cycle")
        self._validate_appraisal(desire, readiness, pressure, agency, stress)
        if predicted_behavior not in BEHAVIORS:
            raise ValueError(f"predicted_behavior must be one of {sorted(BEHAVIORS)}")
        if voluntariness not in VOLUNTARINESS_STATES:
            raise ValueError(f"voluntariness must be one of {sorted(VOLUNTARINESS_STATES)}")
        if alignment not in ALIGNMENT_STATES:
            raise ValueError(f"alignment must be one of {sorted(ALIGNMENT_STATES)}")
        will = {
            "reported_action": self._text(reported_action, "reported_action", 500),
            "desire": desire,
            "readiness": readiness,
            "intended_choice": self._text(intended_choice, "intended_choice", 1000),
            "predicted_behavior": predicted_behavior,
            "voluntariness": voluntariness,
            "alignment": alignment,
            "agency": agency,
            "pressure": pressure,
            "stress": stress,
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "recorded_at": self.clock(),
        }
        cycle["will"] = will
        cycle["phase"] = "commit"
        appraisal = {
            "appraisal_id": f"v{len(state['appraisals']) + 1}",
            "action": cycle["action"],
            "desire": desire,
            "readiness": readiness,
            "pressure": pressure,
            "agency": agency,
            "stress": stress,
            "source": "will",
            "cycle_id": cycle_id,
            "evidence_note": will["evidence_note"],
            "time": self.clock(),
        }
        state["appraisals"].append(appraisal)
        state["current_appraisals"][cycle["action"]] = appraisal
        self._save()
        self._append("cycle_will", {"cycle_id": cycle_id, **will})
        self._append("appraisal", appraisal)
        return self.state()

    @staticmethod
    def _classification(behavior: str, voluntariness: str) -> str:
        if behavior in {"refused", "withdrew", "escaped"}:
            return "resisted"
        if behavior == "froze":
            return "freeze_response"
        if voluntariness in {"coerced", "overridden"}:
            return "complied_under_duress" if behavior in {"accepted", "complied"} else "agency_overridden"
        if voluntariness == "pressured":
            return "pressured_behavior"
        if behavior in {"requested", "accepted", "complied"}:
            return "freely_chosen_behavior"
        return "no_action"

    def decide(self, **_: Any) -> dict[str, Any]:
        raise VolitionError(
            "direct volition decision is disabled; use cycle_begin -> Heart/Head -> Will/Ego -> commit"
        )

    def commit(self, *, cycle_id: str, evidence_note: str) -> dict[str, Any]:
        state, cycle = self._cycle(cycle_id)
        if cycle["phase"] != "commit" or cycle.get("will") is None:
            raise VolitionError("cannot commit before Will/Ego completes the current cycle")
        will = cycle["will"]
        item = {
            "decision_id": f"w{len(state['decisions']) + 1}",
            "cycle_id": cycle_id,
            "action": cycle["action"],
            "intended_choice": will["intended_choice"],
            "behavior": will["predicted_behavior"],
            "voluntariness": will["voluntariness"],
            "desire": will["desire"],
            "readiness": will["readiness"],
            "intention_behavior_alignment": will["alignment"],
            "classification": self._classification(will["predicted_behavior"], will["voluntariness"]),
            "consent_effect": "no_change_separate_explicit_consent_required",
            "agency": will["agency"],
            "pressure": will["pressure"],
            "stress": will["stress"],
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "will_evidence_note": will["evidence_note"],
            "time": self.clock(),
        }
        state["decisions"].append(item)
        cycle["phase"] = "committed"
        cycle["committed_at"] = self.clock()
        cycle["decision_id"] = item["decision_id"]
        state["completed_cycles"].append(cycle)
        state["last_committed_cycle_id"] = cycle_id
        state["active_cycle"] = None
        self._save()
        self._append("decision", item)
        self._append("cycle_commit", {
            "cycle_id": cycle_id,
            "decision_id": item["decision_id"],
            "behavior": item["behavior"],
            "voluntariness": item["voluntariness"],
        })
        return self.state()

    def state(self) -> dict[str, Any]:
        self._expire_if_due()
        if self._state is None:
            raise VolitionError("no Will/Ego session")
        state = self._state
        visible = [item for item in state["audience"] if item["visibility"] == "chorus"]
        cycle = state.get("active_cycle")
        public_cycle = None
        if cycle is not None:
            public_cycle = {
                "cycle_id": cycle["cycle_id"],
                "action": cycle["action"],
                "shared_event": cycle["shared_event"],
                "phase": cycle["phase"],
                "heart_recorded": cycle["voices"]["heart"] is not None,
                "head_recorded": cycle["voices"]["brain"] is not None,
                "will_recorded": cycle.get("will") is not None,
            }
        return {
            "volition_version": VOLITION_VERSION,
            "relationship_session_id": state["relationship_session_id"],
            "character_id": state["character_id"],
            "character_profile_sha256": state["character_profile_sha256"],
            "status": state["status"],
            "time_remaining_seconds": max(0.0, state["deadline_at"] - self.clock()),
            "observer_evaluation_count": sum(
                item["visibility"] == "observer" for item in state["audience"]
            ),
            "recent_chorus": visible[-10:],
            "current_appraisals": state["current_appraisals"],
            "active_cycle": public_cycle,
            "last_committed_cycle_id": state.get("last_committed_cycle_id"),
            "completed_cycle_count": len(state.get("completed_cycles", [])),
            "recent_decisions": state["decisions"][-10:],
        }


__all__ = ["VolitionRuntime", "VolitionError", "VOLITION_VERSION"]
