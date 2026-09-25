"""The model cannot schedule votes, pick results, mutate stats or publish drafts."""
from concurrent.futures import ThreadPoolExecutor
import threading

from . import prompts
from .engine import InvalidReport, reduce_turn, validate_draft, validate_report
from .opencode import BackendError, parse_json


class Runtime:
    def __init__(self, store, backend, rules, manuals="", log=None):
        self.store, self.backend, self.rules, self.manuals = store, backend, rules, manuals
        self.log = log or (lambda _message, _level="INFO": None)
        self.lock = threading.Lock()
        self.thread = None

    def submit(self, event):
        with self.lock:
            if self.thread and self.thread.is_alive() and not self.store.get(event["id"]):
                raise ValueError("Дождись завершения текущего хода")
            if self.store.begin(event):
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
            self.log(f"ход {event['id']}: disposition={disposition}; tone={contract['decision']['tone']}"
                     + ("; MCP разрешён для лабораторного шага" if contract["activity"] == "lab"
                        else "; MCP отключён"))
            self.store.progress(event["id"], "Решение принято", audit)

            # Temporary physical projection until Patch 2 EffectPlan. A laboratory
            # side effect is possible only after request_lab_work + accept.
            if contract["activity"] == "lab":
                self.store.progress(event["id"], "Лаборатория: проверка через MCP", audit)
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
                self.store.progress(event["id"], "Лабораторный шаг завершён", audit)

            text = None
            correction = ""
            for attempt in range(2):
                self.store.progress(
                    event["id"],
                    "Юки подбирает слова" if not attempt else "Проверка формулировки",
                    audit,
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
            if contract["activity"] == "lab":
                after["memories"][-1]["laboratory"] = {
                    "report": audit["laboratory"].get("text", "")[:1600],
                    "uncertain": audit["laboratory"].get("uncertain", False),
                    "tools": [{"tool": item.get("tool"),
                               "status": (item.get("state") or {}).get("status"),
                               "output": str((item.get("state") or {}).get("output", ""))[:2500]}
                              for item in audit["laboratory"].get("tools", [])[-6:]],
                }
            self.store.finish(event["id"], before, after, audit)
        except Exception as exc:
            self.log(f"ход {event['id']}: {exc}", "ERROR")
            self.store.progress(event["id"], "Ход остановлен", audit)
            self.store.fail(event["id"], str(exc))
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
