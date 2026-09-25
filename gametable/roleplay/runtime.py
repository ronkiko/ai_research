"""The model cannot schedule votes, pick results, mutate stats or publish drafts."""
from concurrent.futures import ThreadPoolExecutor
import threading

from . import prompts
from .engine import InvalidReport, reduce_turn, validate_draft, validate_report
from .opencode import BackendError, parse_json


class Runtime:
    def __init__(self, store, backend, rules, manuals="", log=None, events=None):
        self.store, self.backend, self.rules, self.manuals = store, backend, rules, manuals
        self.log = log or (lambda _message, _level="INFO": None)
        self.events = events or (lambda _name, _payload: None)
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

    def run(self, event):
        before = self.store.state()
        self.log(f"ход {event['id']}: intent={event['intent_id']}")
        audit = {"before": before, "model": self.backend.model, "rules": self.rules,
                 "assessments": {}, "draft_attempts": [], "laboratory": {"text": "", "tools": []}}
        try:
            parent = self.backend.create("GameTable turn " + event["id"])
            audit["parent_session"] = parent
            data = prompts.packet(event, before, self.rules)
            requests = {role: prompts.appraisal(role, data) for role in ("heart", "head")}

            def assess(role):
                response = self.backend.complete(parent, "yuki-" + role, requests[role])
                report = validate_report(parse_json(response["text"]), event, before, role)
                return {"report": report, "session_id": response["session_id"]}

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {role: pool.submit(assess, role) for role in requests}
                for role, future in futures.items():
                    audit["assessments"][role] = future.result()

            after, contract, calculations = reduce_turn(
                before, event,
                audit["assessments"]["heart"]["report"],
                audit["assessments"]["head"]["report"],
                self.rules,
            )
            audit.update(after=after, contract=contract, calculations=calculations)
            disposition = contract["decision"]["disposition"]
            external_effects = contract["effect_plan"]["external_effects"]
            laboratory_effect = any(effect.get("type") == "laboratory_step"
                                    for effect in external_effects)
            self.log(f"ход {event['id']}: disposition={disposition}; tone={contract['decision']['tone']}; "
                     f"scene={before['scene_id']}->{after['scene_id']}"
                     + ("; MCP разрешён EffectPlan" if laboratory_effect else "; MCP отключён"))
            self.progress(event["id"], "Решение и эффекты приняты", audit, before["revision"])
            if before["scene_id"] != after["scene_id"]:
                self.emit("scene.transition", event["id"], before["revision"],
                          from_scene=before["scene_id"], to_scene=after["scene_id"],
                          next_revision=after["revision"])

            if laboratory_effect:
                self.progress(event["id"], "Лаборатория: проверка через MCP",
                              audit, before["revision"])
                try:
                    audit["laboratory"] = self.backend.complete(
                        parent, "yuki", prompts.laboratory_task(data, self.manuals), lab=True)
                except BackendError as exc:
                    audit["laboratory"] = {
                        "text": str(exc) +
                            ". Результат операции неизвестен: не повторять запуск без проверки статуса.",
                        "tools": [],
                        "uncertain": True,
                    }
                self.progress(event["id"], "Лабораторный шаг завершён",
                              audit, before["revision"])

            text = None
            correction = ""
            for attempt in range(2):
                self.progress(
                    event["id"],
                    "Юки подбирает слова" if not attempt else "Проверка формулировки",
                    audit,
                    before["revision"],
                )
                record = {}
                try:
                    response = self.backend.complete(
                        parent, "yuki",
                        prompts.narration(data, contract, audit["laboratory"], correction))
                    draft = parse_json(response["text"])
                    record["draft"] = draft
                    record["session_id"] = response["session_id"]
                    candidate = validate_draft(draft, contract)
                    verdict_response = self.backend.complete(
                        parent, "yuki-head",
                        prompts.review(data, contract, draft, audit["laboratory"]))
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

            audit["reply"] = {
                "anchor": contract["anchor"],
                "text": text or "",
                "fallback": text is None,
                "disposition": disposition,
                "tone": contract["decision"]["tone"],
            }
            audit["notice"] = ("Не удалось проверить свободную реплику. Показано только решение движка."
                               if text is None else None)
            after["memories"][-1]["yuki"] = {"anchor": contract["anchor"], "text": text or ""}
            if laboratory_effect:
                after["memories"][-1]["laboratory"] = {
                    "report": audit["laboratory"].get("text", "")[:1600],
                    "uncertain": audit["laboratory"].get("uncertain", False),
                    "tools": [{"tool": item.get("tool"),
                               "status": (item.get("state") or {}).get("status"),
                               "output": str((item.get("state") or {}).get("output", ""))[:2500]}
                              for item in audit["laboratory"].get("tools", [])[-6:]],
                }
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
    """Never expose in-flight or rejected drafts through polling endpoints."""
    item = {k: turn[k] for k in ("id", "event", "status", "stage", "error")}
    if turn["status"] == "done":
        result = turn["result"]
        item.update(reply=result["reply"], contract=result["contract"], notice=result.get("notice"))
        item["delta"] = {k: round(result["after"]["stats"][k] - result["before"]["stats"][k], 2)
                         for k in result["after"]["stats"]}
    return item
