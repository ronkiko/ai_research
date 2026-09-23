"""Persistent Will/Ego and audience journal for a bounded Brain shift.

The runtime records model appraisals and behavior.  It never converts pressure
into desire, consent, or an actuator command and contains no personality score
formula.
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
        if self._state is None or self._state.get("status") != "active":
            raise VolitionError("no active Will/Ego session")
        return self._state

    def begin(
        self,
        *,
        relationship_session_id: str,
        deadline_at: float,
        character_core: dict[str, Any],
    ) -> dict[str, Any]:
        self._expire_if_due()
        if self._state is not None and self._state.get("status") == "active":
            if self._state.get("relationship_session_id") == relationship_session_id:
                if self._state.get("character_profile_sha256") != character_core.get("profile_sha256"):
                    raise VolitionError(
                        "active shift Character Core cannot change without a new relationship session"
                    )
                return self.state()
            self._state["status"] = "superseded"
            self._state["finished_at"] = self.clock()
            self._save()
            self._append("superseded", {"next_relationship_session_id": relationship_session_id})
        if float(deadline_at) <= self.clock():
            raise ValueError("deadline_at must be in the future")
        self._state = {
            "version": VOLITION_VERSION,
            "relationship_session_id": self._text(relationship_session_id, "relationship_session_id", 200),
            "character_id": self._text(character_core.get("character_id"), "character_id", 200),
            "character_profile_sha256": self._text(
                character_core.get("profile_sha256"), "character_profile_sha256", 128,
            ),
            "deadline_at": float(deadline_at),
            "status": "active",
            "started_at": self.clock(),
            "audience": [],
            "appraisals": [],
            "decisions": [],
            "current_appraisals": {},
        }
        self._save()
        self._append("begin", {
            "deadline_at": float(deadline_at),
            "character_core": character_core,
        })
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
        state = self._require_open()
        if desire not in DESIRE_STATES:
            raise ValueError(f"desire must be one of {sorted(DESIRE_STATES)}")
        if readiness not in READINESS_STATES:
            raise ValueError(f"readiness must be one of {sorted(READINESS_STATES)}")
        if pressure not in PRESSURE_LEVELS or stress not in PRESSURE_LEVELS:
            raise ValueError(f"pressure and stress must be one of {sorted(PRESSURE_LEVELS)}")
        if agency not in AGENCY_STATES:
            raise ValueError(f"agency must be one of {sorted(AGENCY_STATES)}")
        item = {
            "appraisal_id": f"v{len(state['appraisals']) + 1}",
            "action": self._text(action, "action", 200),
            "desire": desire,
            "readiness": readiness,
            "pressure": pressure,
            "agency": agency,
            "stress": stress,
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "time": self.clock(),
        }
        state["appraisals"].append(item)
        state["current_appraisals"][item["action"]] = item
        self._save()
        self._append("appraisal", item)
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

    def decide(
        self,
        *,
        action: str,
        intended_choice: str,
        behavior: str,
        voluntariness: str,
        desire: str,
        readiness: str,
        alignment: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        state = self._require_open()
        if behavior not in BEHAVIORS:
            raise ValueError(f"behavior must be one of {sorted(BEHAVIORS)}")
        if voluntariness not in VOLUNTARINESS_STATES:
            raise ValueError(f"voluntariness must be one of {sorted(VOLUNTARINESS_STATES)}")
        if desire not in DESIRE_STATES:
            raise ValueError(f"desire must be one of {sorted(DESIRE_STATES)}")
        if readiness not in READINESS_STATES:
            raise ValueError(f"readiness must be one of {sorted(READINESS_STATES)}")
        if alignment not in ALIGNMENT_STATES:
            raise ValueError(f"alignment must be one of {sorted(ALIGNMENT_STATES)}")
        item = {
            "decision_id": f"w{len(state['decisions']) + 1}",
            "action": self._text(action, "action", 200),
            "intended_choice": self._text(intended_choice, "intended_choice", 500),
            "behavior": behavior,
            "voluntariness": voluntariness,
            "desire": desire,
            "readiness": readiness,
            "intention_behavior_alignment": alignment,
            "classification": self._classification(behavior, voluntariness),
            "consent_effect": "no_change_separate_explicit_consent_required",
            "evidence_note": self._text(evidence_note, "evidence_note"),
            "time": self.clock(),
        }
        state["decisions"].append(item)
        self._save()
        self._append("decision", item)
        return self.state()

    def state(self) -> dict[str, Any]:
        self._expire_if_due()
        if self._state is None:
            raise VolitionError("no Will/Ego session")
        state = self._state
        visible = [item for item in state["audience"] if item["visibility"] == "chorus"]
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
            "recent_decisions": state["decisions"][-10:],
        }


__all__ = ["VolitionRuntime", "VolitionError", "VOLITION_VERSION"]
