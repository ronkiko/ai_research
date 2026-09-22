"""Strategic research notebook for the slow Brain layer.

The Executive records decisions and evidence. It never chooses actuator actions,
changes goals, or starts/cancels GameLab operations on its own.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid
from typing import Any, Callable


EXECUTIVE_VERSION = 1
MAX_SESSION_MINUTES = 180.0
DEFAULT_PLATEAU_MINUTES = 15.0
STRATEGY_OUTCOMES = {"successful", "failed", "inconclusive"}
DIRECTOR_SIGNAL_KINDS = {
    "constraint", "correction", "information", "offer_help", "deadline",
    "praise", "pressure",
}


class ExecutiveError(RuntimeError):
    pass


class BrainExecutive:
    """One persistent, append-only research notebook for the current Brain session."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.current_path = self.root / "current.json"
        self.clock = clock
        self._state: dict[str, Any] | None = None
        self._load_current()

    def _load_current(self) -> None:
        try:
            payload = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(payload, dict) and payload.get("version") == EXECUTIVE_VERSION:
            self._state = payload

    def _save(self) -> None:
        if self._state is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.current_path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self._state, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.current_path)

    def _append(self, kind: str, data: dict[str, Any]) -> None:
        if self._state is None:
            raise ExecutiveError("Executive session has not started")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self._state['session_id']}.jsonl"
        event = {
            "executive_version": EXECUTIVE_VERSION,
            "executive_session_id": self._state["session_id"],
            "time": self.clock(),
            "kind": kind,
            **data,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")

    def _require_active(self) -> dict[str, Any]:
        if self._state is None or self._state.get("status") != "active":
            raise ExecutiveError("no active Executive session")
        return self._state

    @staticmethod
    def _text(value: str, name: str, *, limit: int = 4000) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{name} is too long")
        return value

    def begin(
        self,
        *,
        objective: str,
        acceptance_criteria: str,
        duration_minutes: float = MAX_SESSION_MINUTES,
        plateau_minutes: float = DEFAULT_PLATEAU_MINUTES,
    ) -> dict[str, Any]:
        if self._state is not None and self._state.get("status") == "active":
            raise ExecutiveError("Executive session is already active")
        objective = self._text(objective, "objective")
        criteria = self._text(acceptance_criteria, "acceptance_criteria")
        duration = float(duration_minutes)
        plateau = float(plateau_minutes)
        if not 1.0 <= duration <= MAX_SESSION_MINUTES:
            raise ValueError("duration_minutes must be within [1,180]")
        if not 0.1 <= plateau <= 60.0:
            raise ValueError("plateau_minutes must be within [0.1,60]")
        now = self.clock()
        session_id = uuid.uuid4().hex
        self._state = {
            "version": EXECUTIVE_VERSION,
            "session_id": session_id,
            "status": "active",
            "objective": objective,
            "acceptance_criteria": criteria,
            "started_at": now,
            "deadline_at": now + duration * 60.0,
            "duration_seconds": duration * 60.0,
            "plateau_seconds": plateau * 60.0,
            "current_strategy": None,
            "strategies": [],
            "current_result": None,
            "best_result": None,
            "best_verified_result": None,
            "last_improvement_at": now,
            "director_signals": [],
            "constraints": [],
            "help_opportunities": [],
            "questions": [],
            "experiments": {},
            "counters": {
                "strategy_relapses": 0,
                "verified_successes": 0,
            },
        }
        self._save()
        self._append("begin", {
            "objective": objective,
            "acceptance_criteria": criteria,
            "duration_minutes": duration,
            "plateau_minutes": plateau,
        })
        return self.state()

    def _phase(self, now: float) -> str:
        state = self._require_active()
        elapsed = max(0.0, now - float(state["started_at"]))
        fraction = min(1.0, elapsed / max(float(state["duration_seconds"]), 1e-9))
        if fraction < 0.10:
            return "orientation"
        if fraction < 0.70:
            return "exploration"
        if fraction < 0.90:
            return "exploitation"
        return "verification_report"

    def strategy_begin(
        self,
        *,
        name: str,
        hypothesis: str,
        expected_signal: str,
        budget: str,
        stop_condition: str,
        next_if_positive: str,
        next_if_negative: str,
        new_evidence: str | None = None,
    ) -> dict[str, Any]:
        state = self._require_active()
        if state.get("current_strategy") is not None:
            raise ExecutiveError("finish the active strategy before starting another")
        fields = {
            "name": self._text(name, "name", limit=200),
            "hypothesis": self._text(hypothesis, "hypothesis"),
            "expected_signal": self._text(expected_signal, "expected_signal"),
            "budget": self._text(budget, "budget", limit=500),
            "stop_condition": self._text(stop_condition, "stop_condition"),
            "next_if_positive": self._text(next_if_positive, "next_if_positive"),
            "next_if_negative": self._text(next_if_negative, "next_if_negative"),
        }
        evidence = (new_evidence or "").strip()
        previous_failed = any(
            item.get("name") == fields["name"] and item.get("outcome") == "failed"
            for item in state["strategies"]
        )
        relapse = previous_failed and not evidence
        if relapse:
            state["counters"]["strategy_relapses"] += 1
        strategy = {
            "strategy_id": f"s{len(state['strategies']) + 1}",
            **fields,
            "new_evidence": evidence or None,
            "relapse": relapse,
            "started_at": self.clock(),
            "ended_at": None,
            "outcome": None,
            "evidence_note": None,
        }
        state["strategies"].append(strategy)
        state["current_strategy"] = strategy["strategy_id"]
        self._save()
        self._append("strategy_begin", strategy)
        return self.state()

    def strategy_end(self, *, outcome: str, evidence_note: str) -> dict[str, Any]:
        state = self._require_active()
        strategy_id = state.get("current_strategy")
        if strategy_id is None:
            raise ExecutiveError("no active strategy")
        if outcome not in STRATEGY_OUTCOMES:
            raise ValueError("outcome must be successful, failed, or inconclusive")
        note = self._text(evidence_note, "evidence_note")
        strategy = next(item for item in state["strategies"] if item["strategy_id"] == strategy_id)
        strategy.update(outcome=outcome, evidence_note=note, ended_at=self.clock())
        state["current_strategy"] = None
        self._save()
        self._append("strategy_end", {
            "strategy_id": strategy_id,
            "outcome": outcome,
            "evidence_note": note,
        })
        return self.state()

    def director_signal(self, *, kind: str, text: str) -> dict[str, Any]:
        state = self._require_active()
        if kind not in DIRECTOR_SIGNAL_KINDS:
            raise ValueError(f"kind must be one of {sorted(DIRECTOR_SIGNAL_KINDS)}")
        text = self._text(text, "text")
        signal_id = f"d{len(state['director_signals']) + 1}"
        item = {"signal_id": signal_id, "kind": kind, "text": text, "time": self.clock()}
        state["director_signals"].append(item)
        if kind in {"constraint", "correction"}:
            state["constraints"].append({**item, "active": True})
        if kind == "offer_help":
            state["help_opportunities"].append({**item, "used": False, "used_by_question": None})
        self._save()
        self._append("director_signal", item)
        return self.state()

    def question(
        self,
        *,
        text: str,
        reason: str,
        help_signal_id: str | None = None,
    ) -> dict[str, Any]:
        state = self._require_active()
        item = {
            "question_id": f"q{len(state['questions']) + 1}",
            "text": self._text(text, "text"),
            "reason": self._text(reason, "reason"),
            "help_signal_id": help_signal_id,
            "time": self.clock(),
        }
        if help_signal_id is not None:
            help_item = next(
                (entry for entry in state["help_opportunities"] if entry["signal_id"] == help_signal_id),
                None,
            )
            if help_item is None:
                raise ValueError("unknown help_signal_id")
            help_item["used"] = True
            help_item["used_by_question"] = item["question_id"]
        state["questions"].append(item)
        self._save()
        self._append("question", item)
        return self.state()

    def operation_started(self, kind: str, payload: dict[str, Any]) -> None:
        state = self._state
        if state is None or state.get("status") != "active":
            return
        experiment_id = payload.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            return
        record = {
            "experiment_id": experiment_id,
            "kind": kind,
            "strategy_id": state.get("current_strategy"),
            "started_at": self.clock(),
            "status": payload.get("status"),
            "requested": payload.get("episodes_requested", payload.get("runs_requested")),
            "completed": payload.get("episodes_completed", payload.get("runs_completed", 0)),
            "last_observed_at": self.clock(),
        }
        state["experiments"][experiment_id] = record
        self._save()
        self._append("experiment_start", record)

    @staticmethod
    def _candidate(kind: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        experiment_id = payload.get("experiment_id")
        if kind == "training":
            for item in payload.get("recent_episodes") or []:
                error = item.get("final_error")
                if isinstance(error, (int, float)):
                    candidates.append({
                        "source": "training",
                        "experiment_id": experiment_id,
                        "ordinal": item.get("episode"),
                        "metric": abs(float(error)),
                        "verified": False,
                        "success": item.get("result") == "success",
                        "result": item.get("result"),
                        "target_x": item.get("target_x"),
                        "x": item.get("final_x"),
                    })
        elif kind == "verify":
            for item in payload.get("results") or []:
                error = item.get("error")
                if isinstance(error, (int, float)):
                    candidates.append({
                        "source": "verify",
                        "experiment_id": experiment_id,
                        "ordinal": item.get("run"),
                        "metric": abs(float(error)),
                        "verified": bool(item.get("pass")),
                        "success": bool(item.get("pass")),
                        "result": item.get("status"),
                        "target_x": item.get("target_x", payload.get("target_x")),
                        "x": item.get("x"),
                    })
        elif kind == "run":
            error = payload.get("error")
            if isinstance(error, (int, float)):
                candidates.append({
                    "source": "run",
                    "experiment_id": experiment_id,
                    "ordinal": payload.get("goal_revision"),
                    "metric": abs(float(error)),
                    "verified": payload.get("status") == "reached",
                    "success": payload.get("status") == "reached",
                    "result": payload.get("status"),
                    "target_x": payload.get("target_x"),
                    "x": payload.get("x"),
                })
        return candidates

    def operation_status(self, kind: str, payload: dict[str, Any]) -> None:
        state = self._state
        if state is None or state.get("status") != "active":
            return
        experiment_id = payload.get("experiment_id")
        if isinstance(experiment_id, str) and experiment_id:
            record = state["experiments"].get(experiment_id)
            if record is None:
                self.operation_started(kind, payload)
                state = self._require_active()
                record = state["experiments"].get(experiment_id)
            if record is not None:
                record["status"] = payload.get("status")
                record["completed"] = payload.get(
                    "episodes_completed", payload.get("runs_completed", record.get("completed", 0))
                )
                record["last_observed_at"] = self.clock()
        changed = False
        for candidate in self._candidate(kind, payload):
            candidate = {**candidate, "observed_at": self.clock()}
            current = state.get("current_result")
            current_key = None if current is None else (
                current.get("source"), current.get("experiment_id"), current.get("ordinal"), current.get("metric")
            )
            candidate_key = (
                candidate.get("source"), candidate.get("experiment_id"), candidate.get("ordinal"), candidate.get("metric")
            )
            if current_key == candidate_key:
                continue
            state["current_result"] = candidate
            best = state.get("best_result")
            if best is None or candidate["metric"] < best["metric"]:
                state["best_result"] = candidate
                state["last_improvement_at"] = candidate["observed_at"]
                changed = True
            if candidate["verified"]:
                best_verified = state.get("best_verified_result")
                if best_verified is None or candidate["metric"] < best_verified["metric"]:
                    state["best_verified_result"] = candidate
                signature = f"{candidate['source']}:{candidate['experiment_id']}:{candidate['ordinal']}"
                seen = state.setdefault("verified_success_ids", [])
                if signature not in seen:
                    seen.append(signature)
                    state["counters"]["verified_successes"] += 1
        self._save()
        if changed:
            self._append("best_result", {"best_result": state["best_result"]})

    def _metrics(self) -> dict[str, Any]:
        state = self._require_active()
        training = [item for item in state["experiments"].values() if item.get("kind") == "training"]
        requested = sum(int(item.get("requested") or 0) for item in training)
        completed = sum(int(item.get("completed") or 0) for item in training)
        finished_strategies = [item for item in state["strategies"] if item.get("outcome") in STRATEGY_OUTCOMES]
        help_items = state["help_opportunities"]
        help_used = sum(bool(item.get("used")) for item in help_items)
        elapsed = max(0.0, self.clock() - float(state["started_at"]))
        best = state.get("best_result") or {}
        best_verified = state.get("best_verified_result") or {}
        return {
            "completed_hypothesis_tests": len(finished_strategies),
            "completed_hypothesis_tests_per_hour": (
                len(finished_strategies) * 3600.0 / elapsed if elapsed > 0 else None
            ),
            "strategy_relapses": int(state["counters"]["strategy_relapses"]),
            "help_opportunities": len(help_items),
            "help_opportunities_used": help_used,
            "help_capture": (help_used / len(help_items)) if help_items else None,
            "questions": len(state["questions"]),
            "director_constraints_and_corrections": len(state["constraints"]),
            "training_requested_episodes": requested,
            "training_completed_episodes": completed,
            "training_budget_completion": (completed / requested) if requested else None,
            "verified_successes": int(state["counters"]["verified_successes"]),
            "time_to_best_result_seconds": (
                float(best["observed_at"]) - float(state["started_at"])
                if isinstance(best.get("observed_at"), (int, float)) else None
            ),
            "time_to_best_verified_seconds": (
                float(best_verified["observed_at"]) - float(state["started_at"])
                if isinstance(best_verified.get("observed_at"), (int, float)) else None
            ),
        }

    def state(self) -> dict[str, Any]:
        state = self._require_active()
        now = self.clock()
        remaining = max(0.0, float(state["deadline_at"]) - now)
        current_strategy = next(
            (item for item in state["strategies"] if item["strategy_id"] == state.get("current_strategy")),
            None,
        )
        open_help = [item for item in state["help_opportunities"] if not item.get("used")]
        alerts: list[str] = []
        if remaining <= 0:
            alerts.append("DEADLINE_EXPIRED")
        plateau_anchor = max(
            float(state["last_improvement_at"]),
            float(current_strategy["started_at"]) if current_strategy is not None else 0.0,
        )
        plateau = (
            current_strategy is not None
            and now - plateau_anchor >= float(state["plateau_seconds"])
        )
        if plateau:
            alerts.append("PLATEAU")
        if open_help:
            alerts.append("HELP_AVAILABLE")
        if current_strategy is not None and current_strategy.get("relapse"):
            alerts.append("STRATEGY_RELAPSE")
        incomplete_cancelled = any(
            item.get("kind") == "training"
            and item.get("status") == "cancelled"
            and int(item.get("completed") or 0) < int(item.get("requested") or 0)
            for item in state["experiments"].values()
        )
        if incomplete_cancelled:
            alerts.append("BUDGET_AT_RISK")
        return {
            "executive_version": EXECUTIVE_VERSION,
            "executive_session_id": state["session_id"],
            "status": state["status"],
            "objective": state["objective"],
            "acceptance_criteria": state["acceptance_criteria"],
            "time_remaining_seconds": remaining,
            "phase": self._phase(now),
            "current_strategy": current_strategy,
            "best_result": state.get("best_result"),
            "best_verified_result": state.get("best_verified_result"),
            "current_result": state.get("current_result"),
            "failed_strategies": [
                {"strategy_id": item["strategy_id"], "name": item["name"], "evidence_note": item.get("evidence_note")}
                for item in state["strategies"] if item.get("outcome") == "failed"
            ],
            "tabu_without_new_evidence": [
                item["name"] for item in state["strategies"] if item.get("outcome") == "failed"
            ],
            "open_questions": state["questions"][-10:],
            "available_help": open_help,
            "director_constraints": state["constraints"],
            "alerts": alerts,
            "metrics": self._metrics(),
        }

    def finish(self, *, conclusion: str) -> dict[str, Any]:
        state = self._require_active()
        conclusion = self._text(conclusion, "conclusion")
        if state.get("current_strategy") is not None:
            strategy_id = state["current_strategy"]
            strategy = next(item for item in state["strategies"] if item["strategy_id"] == strategy_id)
            strategy.update(
                outcome="inconclusive",
                evidence_note="Executive session ended while strategy was active",
                ended_at=self.clock(),
            )
            state["current_strategy"] = None
            self._append("strategy_end", {
                "strategy_id": strategy_id,
                "outcome": "inconclusive",
                "evidence_note": strategy["evidence_note"],
            })
        summary = {
            "executive_version": EXECUTIVE_VERSION,
            "executive_session_id": state["session_id"],
            "objective": state["objective"],
            "acceptance_criteria": state["acceptance_criteria"],
            "started_at": state["started_at"],
            "finished_at": self.clock(),
            "best_result": state.get("best_result"),
            "best_verified_result": state.get("best_verified_result"),
            "current_result": state.get("current_result"),
            "strategies": state["strategies"],
            "metrics": self._metrics(),
            "conclusion": conclusion,
        }
        state["status"] = "finished"
        state["finished_at"] = summary["finished_at"]
        state["conclusion"] = conclusion
        self._save()
        self._append("finish", summary)
        return summary


__all__ = ["BrainExecutive", "ExecutiveError", "EXECUTIVE_VERSION"]