"""Persistent Heart–Brain conflict memory for a bounded Brain shift.

Exact confidences are private research telemetry.  The character-facing MCP
surface deliberately exposes only four coarse quarters, so neither voice can
calculate the other voice's exact strength or deterministically farm a result.
"""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import time
from typing import Any, Callable


DUALITY_VERSION = 1
SIDES = {"heart", "brain"}
APPRAISAL_DIRECTIONS = {"strengthen", "weaken"}
APPRAISAL_INTENSITIES = {
    "faint": (1, 4),
    "meaningful": (5, 10),
    "strong": (11, 18),
    "decisive": (19, 30),
}
RESOLUTIONS = {"heart", "brain", "compromise"}
OUTCOMES = {"won", "lost", "mixed", "unresolved"}


class DualityError(RuntimeError):
    pass


class DualityRuntime:
    """Append-only internal conflict ledger with deliberately blurred reads."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
        randint: Callable[[int, int], int] = secrets.SystemRandom().randint,
    ) -> None:
        self.root = Path(root)
        self.current_path = self.root / "duality-current.json"
        self.clock = clock
        self.randint = randint
        self._state: dict[str, Any] | None = None
        self._load()

    def _load(self) -> None:
        try:
            state = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if isinstance(state, dict) and state.get("version") == DUALITY_VERSION:
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
            raise DualityError("Heart–Brain shift has not started")
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "duality_version": DUALITY_VERSION,
            "executive_session_id": self._state["executive_session_id"],
            "time": self.clock(),
            "kind": kind,
            **payload,
        }
        path = self.root / f"{self._state['executive_session_id']}.duality.jsonl"
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

    @staticmethod
    def _blur(value: int) -> dict[str, Any]:
        if value >= 100:
            return {"level": "4/4", "all_in_available": True}
        if value >= 75:
            return {"level": "3/4", "all_in_available": False}
        if value >= 50:
            return {"level": "2/4", "all_in_available": False}
        if value >= 25:
            return {"level": "1/4", "all_in_available": False}
        return {"level": "0/4", "all_in_available": False}

    def _require_active(self) -> dict[str, Any]:
        if self._state is None:
            raise DualityError("Heart–Brain state has not started")
        if self._state.get("status") == "active" and self.clock() >= float(self._state["deadline_at"]):
            self._expire()
        if self._state.get("status") not in {"active", "deadline_finished"}:
            raise DualityError("Heart–Brain state is unavailable")
        return self._state

    def _expire(self) -> None:
        state = self._state
        if state is None or state.get("status") != "active":
            return
        state["status"] = "deadline_finished"
        state["finished_at"] = float(state["deadline_at"])
        self._save()
        self._append("deadline_finish", {
            "finished_at": state["finished_at"],
            "active_conflict_closed": False,
        })

    def begin(self, *, executive_session_id: str, deadline_at: float) -> dict[str, Any]:
        session_id = self._text(executive_session_id, "executive_session_id", 200)
        now = self.clock()
        deadline = float(deadline_at)
        if self._state is not None and self._state.get("status") in {"active", "deadline_finished"}:
            if self._state.get("executive_session_id") == session_id:
                return self.state()
            previous_id = self._state.get("executive_session_id")
            self._state["status"] = "superseded"
            self._state["finished_at"] = now
            self._save()
            self._append("superseded", {
                "previous_executive_session_id": previous_id,
                "next_executive_session_id": session_id,
            })
        self._state = {
            "version": DUALITY_VERSION,
            "executive_session_id": session_id,
            "status": "active",
            "started_at": now,
            "deadline_at": deadline,
            "confidence": {"heart": 0, "brain": 0},
            "positions": {"heart": None, "brain": None},
            "appraisals": [],
            "active_conflict": None,
            "conflicts": [],
        }
        self._save()
        self._append("begin", {"deadline_at": deadline})
        return self.state()

    def appraise(
        self,
        *,
        side: str,
        direction: str,
        intensity: str,
        position: str,
        evidence_note: str,
    ) -> dict[str, Any]:
        state = self._require_active()
        if side not in SIDES:
            raise ValueError("side must be heart or brain")
        if direction not in APPRAISAL_DIRECTIONS:
            raise ValueError("direction must be strengthen or weaken")
        if intensity not in APPRAISAL_INTENSITIES:
            raise ValueError(f"intensity must be one of {sorted(APPRAISAL_INTENSITIES)}")
        claim = self._text(position, "position")
        note = self._text(evidence_note, "evidence_note")
        low, high = APPRAISAL_INTENSITIES[intensity]
        magnitude = int(self.randint(low, high))
        delta = magnitude if direction == "strengthen" else -magnitude
        before = int(state["confidence"][side])
        after = max(0, min(100, before + delta))
        state["confidence"][side] = after
        state["positions"][side] = claim
        item = {
            "appraisal_id": f"p{len(state['appraisals']) + 1}",
            "side": side,
            "direction": direction,
            "intensity": intensity,
            "position": claim,
            "evidence_note": note,
            "time": self.clock(),
        }
        state["appraisals"].append(item)
        self._save()
        self._append("appraisal", {**item, "private_before": before, "private_delta": delta, "private_after": after})
        return self.state()

    def conflict_begin(self, *, question: str, stakes: str, all_in_by: str = "none") -> dict[str, Any]:
        state = self._require_active()
        if state.get("active_conflict") is not None:
            raise DualityError("resolve the active conflict before starting another")
        if all_in_by not in {"none", *SIDES}:
            raise ValueError("all_in_by must be none, heart, or brain")
        if state["positions"]["heart"] is None or state["positions"]["brain"] is None:
            raise DualityError("both heart and brain need a current position")
        if all_in_by != "none" and int(state["confidence"][all_in_by]) != 100:
            raise DualityError(f"{all_in_by} ALL_IN requires private confidence 100")
        conflict = {
            "conflict_id": f"c{len(state['conflicts']) + 1}",
            "question": self._text(question, "question"),
            "stakes": self._text(stakes, "stakes"),
            "all_in_by": all_in_by,
            "heart_position": state["positions"]["heart"],
            "brain_position": state["positions"]["brain"],
            "private_heart_confidence": int(state["confidence"]["heart"]),
            "private_brain_confidence": int(state["confidence"]["brain"]),
            "started_at": self.clock(),
            "resolution": None,
            "decision": None,
            "rationale": None,
            "resolved_at": None,
            "external_outcome": None,
            "external_evidence": None,
        }
        state["active_conflict"] = conflict
        self._save()
        self._append("conflict_begin", conflict)
        return self.state()

    def resolve(self, *, resolution: str, decision: str, rationale: str) -> dict[str, Any]:
        state = self._require_active()
        conflict = state.get("active_conflict")
        if conflict is None:
            raise DualityError("no active conflict")
        if resolution not in RESOLUTIONS:
            raise ValueError("resolution must be heart, brain, or compromise")
        if conflict["all_in_by"] != "none" and resolution == "compromise":
            raise DualityError("ALL_IN excludes a safe compromise")
        conflict.update(
            resolution=resolution,
            decision=self._text(decision, "decision"),
            rationale=self._text(rationale, "rationale"),
            resolved_at=self.clock(),
        )
        if conflict["all_in_by"] in SIDES:
            state["confidence"][conflict["all_in_by"]] = 0
        self._save()
        self._append("conflict_resolution", conflict)
        return self.state()

    def outcome(self, *, outcome: str, evidence_note: str) -> dict[str, Any]:
        state = self._require_active()
        conflict = state.get("active_conflict")
        if conflict is None or conflict.get("resolution") is None:
            raise DualityError("resolve the internal conflict before recording its external outcome")
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
        conflict.update(external_outcome=outcome, external_evidence=self._text(evidence_note, "evidence_note"))
        state["conflicts"].append(conflict)
        state["active_conflict"] = None
        self._save()
        self._append("external_outcome", {
            "conflict_id": conflict["conflict_id"],
            "outcome": outcome,
            "evidence_note": conflict["external_evidence"],
        })
        return self.state()

    def state(self) -> dict[str, Any]:
        if self._state is None:
            raise DualityError("Heart–Brain shift has not started")
        if self._state.get("status") == "active" and self.clock() >= float(self._state["deadline_at"]):
            self._expire()
        state = self._state
        public_confidence = {side: self._blur(int(state["confidence"][side])) for side in SIDES}
        active = state.get("active_conflict")
        public_conflict = None
        if active is not None:
            public_conflict = {
                key: value for key, value in active.items()
                if not key.startswith("private_")
            }
        return {
            "duality_version": DUALITY_VERSION,
            "executive_session_id": state["executive_session_id"],
            "status": state["status"],
            "time_remaining_seconds": max(0.0, float(state["deadline_at"]) - self.clock()),
            "confidence_telemetry": public_confidence,
            "positions": state["positions"],
            "active_conflict": public_conflict,
            "completed_conflicts": len(state["conflicts"]),
        }


__all__ = ["DualityRuntime", "DualityError", "DUALITY_VERSION"]
