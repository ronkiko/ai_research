"""Explicit GameTable turn pipeline. Models appraise/speak; pure code owns world state."""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading

from . import prompts
from .engine import (InvalidReport, apply_effect_plan, build_contract, build_effect_plan,
                     calculation_audit, decide_turn, validate_draft, validate_report)
from .external import ExternalExecutor
from .opencode import BackendError, parse_json


class Runtime:
    def __init__(self, store, backend, rules, manuals="", log=None, events=None, external=None):
        self.store, self.backend, self.rules, self.manuals = store, backend, rules, manuals
        self.log = log or (lambda _message, _level="INFO": None)
        self.events = events or (lambda _name, _payload: None)
        self.external = external or ExternalExecutor(backend, manuals)
        self.lock = threading.Lock()
        self.thread = None

    def emit(self, name, event_id, revision, **payload):
        self.events(name, {"event_id": event_id, "revision": revision, **payload})

    def progress(self, event_id, stage, payload, revision):
        self.store.progress(event_id, stage, payload)
        self.emit("turn.stage", event_id, revision, stage=stage)

    def submit(self, event):
        with self.lock:
            if self.thread and self.thread.is_alive() and not self.store.get(event["id"]):
                raise ValueError("Дождись завершения текущего хода")
            if self.store.begin(event):
                self.emit("turn.started", event["id"], self.store.state()["revision"],
                          stage="Оценки сердца и головы")
                self.thread = threading.Thread(target=self.run, args=(event,), daemon=True)
                self.thread.start()
        return self.store.get(event["id"])

    def appraise(self, parent, event, before, data):
        requests = {role: prompts.appraisal(role, data) for role in ("heart", "head")}

        def assess(role):
            response = self.backend.complete(parent, "yuki-" + role, requests[role])
            report = validate_report(parse_json(response["text"]), event, before, role)
            return {"report": report, "session_id": response["session_id"]}

        assessments = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {role: pool.submit(assess, role) for role in requests}
            for role, future in futures.items():
                assessments[role] = future.result()
        return assessments

    def execute_external(self, event, parent, data, effect_plan, audit, revision):
        effects = effect_plan["external_effects"]
        audit["external"] = {"planned": copy.deepcopy(effects), "results": []}
        if not effects:
            return []

        # This durable progress record happens before the MCP-enabled call.
        # On crash Store marks the turn failed; Runtime never auto-replays it.
        self.progress(event["id"], "Внешние эффекты подтверждены", audit, revision)
        results = self.external.execute_all(parent, data, effects)
        audit["external"]["results"] = copy.deepcopy(results)
        self.progress(event["id"], "Внешние эффекты наблюдены", audit, revision)
        return results

    def verbalize(self, parent, event, contract, facts, audit, revision):
        text = None
        correction = ""
        for attempt in range(2):
            self.progress(event["id"],
                          "Юки подбирает слова" if not attempt else "Проверка формулировки",
                          audit, revision)
            record = {}
            try:
                response = self.backend.complete(
                    parent, "yuki", prompts.narration(facts, correction))
                draft = parse_json(response["text"])
                record["draft"] = draft
                record["session_id"] = response["session_id"]
                candidate = validate_draft(draft, contract)

                verdict_response = self.backend.complete(
                    parent, "yuki-head", prompts.review(facts, draft))
                verdict = parse_json(verdict_response["text"])
                record["review"] = verdict
                record["review_session"] = verdict_response["session_id"]
                if (not isinstance(verdict, dict)
                        or set(verdict) != {"event_id", "ok", "reason"}
                        or verdict["event_id"] != event["id"]
                        or type(verdict["ok"]) is not bool
                        or not isinstance(verdict["reason"], str)):
                    raise InvalidReport("Неверный формат проверки")
                if not verdict["ok"]:
                    raise InvalidReport(verdict["reason"][:700])
                text = candidate
            except (InvalidReport, BackendError) as exc:
                correction = str(exc)
                record["rejected"] = correction
            audit["draft_attempts"].append(record)
            if text is not None:
                break
        return text

    def run(self, event):
        before = self.store.state()
        self.log(f"ход {event['id']}: intent={event['intent_id']}")
        audit = {"before": before, "model": self.backend.model, "rules": self.rules,
                 "assessments": {}, "draft_attempts": [],
                 "external": {"planned": [], "results": []}}
        try:
            # 1. Freeze input snapshot.
            parent = self.backend.create("GameTable turn " + event["id"])
            audit["parent_session"] = parent
            data = prompts.packet(event, before, self.rules)

            # 2. Independent Heart/Head appraisal.
            audit["assessments"] = self.appraise(parent, event, before, data)

            # 3. DecisionEngine.
            decision, context = decide_turn(
                before, event,
                audit["assessments"]["heart"]["report"],
                audit["assessments"]["head"]["report"],
                self.rules)

            # 4. EffectPlanner.
            effect_plan = build_effect_plan(before, event, decision, context, self.rules)

            # 5. WorldReducer builds provisional after-state; SQLite is unchanged.
            after, world_audit = apply_effect_plan(
                before, event, decision, effect_plan, context, self.rules)
            contract = build_contract(event, after, decision, effect_plan, context, self.rules)
            calculations = calculation_audit(decision, effect_plan, context, world_audit)
            audit.update(after=after, contract=contract, calculations=calculations)
            disposition = decision["disposition"]
            self.log(f"ход {event['id']}: disposition={disposition}; tone={decision['tone']}; "
                     f"scene={before['scene_id']}->{after['scene_id']}; "
                     f"external={len(effect_plan['external_effects'])}")
            self.progress(event["id"], "Решение и эффекты приняты", audit, before["revision"])
            if before["scene_id"] != after["scene_id"]:
                self.emit("scene.transition", event["id"], before["revision"],
                          from_scene=before["scene_id"], to_scene=after["scene_id"],
                          next_revision=after["revision"])

            # 6-7. Persist intent, then execute only typed external effects.
            external_results = self.execute_external(
                event, parent, data, effect_plan, audit, before["revision"])

            # 8. Narrator sees only fixed facts and observed external results.
            facts = prompts.narration_facts(
                data, before, contract, world_audit, after, external_results)
            audit["narration_facts"] = facts

            # 9. Fresh Head review cannot alter fixed facts or state.
            text = self.verbalize(
                parent, event, contract, facts, audit, before["revision"])

            audit["reply"] = {
                "anchor": contract["anchor"],
                "text": text or "",
                "fallback": text is None,
                "disposition": disposition,
                "tone": decision["tone"],
            }
            audit["notice"] = ("Не удалось проверить свободную реплику. Показано только решение движка."
                               if text is None else None)
            after["memories"][-1]["yuki"] = {"anchor": contract["anchor"], "text": text or ""}
            if external_results:
                after["memories"][-1]["external_results"] = [{
                    "effect": copy.deepcopy(item["effect"]),
                    "status": item["status"],
                    "uncertain": item["uncertain"],
                    "report": item.get("report", "")[:1600],
                    "tools": [{"tool": tool.get("tool"),
                               "status": (tool.get("state") or {}).get("status"),
                               "output": str((tool.get("state") or {}).get("output", ""))[:2500]}
                              for tool in item.get("tools", [])[-6:]],
                } for item in external_results]

            # 10. Publish GameState + reviewed reply atomically.
            self.store.finish(event["id"], before, after, audit)
            self.emit("state.changed", event["id"], after["revision"],
                      scene_id=after["scene_id"])
            self.emit("turn.completed", event["id"], after["revision"])
        except Exception as exc:
            self.log(f"ход {event['id']}: {exc}", "ERROR")
            self.progress(event["id"], "Ход остановлен", audit, before["revision"])
            self.store.fail(event["id"], str(exc))
            self.emit("turn.failed", event["id"], self.store.state()["revision"],
                      error=str(exc))
        finally:
            self.backend.close_sessions()


def public_turn(turn):
    """Never expose in-flight or rejected drafts through browser endpoints."""
    item = {k: turn[k] for k in ("id", "event", "status", "stage", "error")}
    if turn["status"] == "done":
        result = turn["result"]
        item.update(reply=result["reply"], contract=result["contract"], notice=result.get("notice"))
        item["delta"] = {k: round(result["after"]["stats"][k] - result["before"]["stats"][k], 2)
                         for k in result["after"]["stats"]}
    return item
