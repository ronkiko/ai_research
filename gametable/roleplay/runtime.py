"""GameTable dialogue + character-action orchestration.

Models appraise/propose/speak. Pure code owns character state; world/navigation
owns physical movement. A turn may start an asynchronous body action but never
waits for arrival.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import uuid

from . import prompts
from .engine import (
    InvalidReport, apply_effect_plan, build_contract, build_effect_plan,
    calculation_audit, decide_turn, validate_action_proposal, validate_draft,
    validate_report,
)
from .external import ACTIVE_NAVIGATION, ActionExecutor
from .opencode import BackendError, parse_json


class Runtime:
    def __init__(
        self, store, backend, rules, manuals="", log=None, events=None,
        external=None, actions=None,
    ):
        self.store, self.backend, self.rules, self.manuals = store, backend, rules, manuals
        self.log = log or (lambda _message, _level="INFO": None)
        self.events = events or (lambda _name, _payload: None)
        # external is retained as an injection alias for older tests/callers.
        self.actions = actions or external or ActionExecutor()
        self.lock = threading.Lock()
        self.thread = None
        self.story = None

    def emit(self, name, event_id, revision, **payload):
        self.events(name, {"event_id": event_id, "revision": revision, **payload})

    def progress(self, event_id, stage, payload, revision):
        self.store.progress(event_id, stage, payload)
        self.emit("turn.stage", event_id, revision, stage=stage)

    def busy(self):
        with self.lock:
            return bool(self.thread and self.thread.is_alive())

    def attach_story(self, story):
        self.story = story

    def submit(self, event):
        with self.lock:
            if self.thread and self.thread.is_alive() and not self.store.get(event["id"]):
                raise ValueError("Дождись завершения текущего хода")
            if self.store.begin(event):
                if self.story is not None:
                    self.story.note_director_message(event)
                self.emit(
                    "turn.started", event["id"], self.store.state()["revision"],
                    stage="Оценки сердца и головы",
                )
                self.thread = threading.Thread(
                    target=self.run, args=(event,), daemon=True
                )
                self.thread.start()
        return self.store.get(event["id"])

    def submit_story_offer(self, offer_id):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            story = self.store.story_state()
            intro = story.get("intro") or {}
            if (
                intro.get("offer_id") != offer_id
                or intro.get("offer_due") is not True
                or intro.get("offer_published") is True
            ):
                return False
            self.thread = threading.Thread(
                target=self._run_story_offer,
                args=(offer_id,),
                daemon=True,
                name=f"story-offer-{offer_id}",
            )
            self.thread.start()
            return True

    def _run_story_offer(self, offer_id):
        try:
            story = self.store.story_state()
            intro = story.get("intro") or {}
            if (
                intro.get("offer_id") != offer_id
                or intro.get("offer_published") is True
            ):
                return
            state = self.store.state()
            world = self.store.latest_world_observation()
            parent = self.backend.create("GameTable story offer " + offer_id)
            response = self.backend.complete(
                parent,
                "yuki",
                prompts.escort_offer(
                    offer_id, state, world, self.store.dialogue()
                ),
            )
            draft = parse_json(response["text"])
            if (
                not isinstance(draft, dict)
                or set(draft) != {"offer_id", "text"}
                or draft.get("offer_id") != offer_id
                or not isinstance(draft.get("text"), str)
                or not 1 <= len(draft["text"].strip()) <= 1600
            ):
                raise InvalidReport("Неверная сюжетная просьба Юки")
            text = draft["text"].strip()
            review_response = self.backend.complete(
                parent,
                "yuki-head",
                prompts.escort_offer_review(offer_id, state, world, draft),
            )
            verdict = parse_json(review_response["text"])
            if (
                not isinstance(verdict, dict)
                or set(verdict) != {"offer_id", "ok", "reason"}
                or verdict.get("offer_id") != offer_id
                or type(verdict.get("ok")) is not bool
                or not isinstance(verdict.get("reason"), str)
            ):
                raise InvalidReport("Неверная проверка сюжетной просьбы")
            if not verdict["ok"]:
                raise InvalidReport(verdict["reason"][:700])

            self.store.publish_story_message(
                f"dialogue.{offer_id}.yuki",
                "character.yuki",
                text,
                turn_id=f"story.{offer_id}",
            )
            current = self.store.story_state()
            current_intro = current["intro"]
            if current_intro.get("offer_id") != offer_id:
                return
            current_intro.update(
                phase="escort_offer_published",
                offer_published=True,
                offer_text=text,
            )
            current["intro"] = current_intro
            self.store.set_story_state(
                current,
                expected_revision=current.get("story_revision"),
            )
            self.events("story.offer_published", {
                "offer_id": offer_id,
                "day_id": current.get("day_id"),
            })
        except Exception as exc:
            try:
                current = self.store.story_state()
                intro = current.get("intro") or {}
                if intro.get("offer_id") == offer_id and not intro.get("offer_published"):
                    intro["offer_attempts"] = int(intro.get("offer_attempts", 0)) + 1
                    intro["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
                    current["intro"] = intro
                    self.store.set_story_state(
                        current,
                        expected_revision=current.get("story_revision"),
                    )
            except Exception:
                pass
            self.log(f"story offer {offer_id}: {exc}", "ERROR")
        finally:
            self.backend.close_sessions()

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

    def propose_self_action(self, parent, data):
        """One tool-less Brain proposal. Scheduler/cooldown remains outside this call."""
        response = self.backend.complete(
            parent, "yuki", prompts.action_proposal(data)
        )
        value = parse_json(response["text"])
        if not isinstance(value, dict) or set(value) != {"proposal"}:
            raise InvalidReport("Неверный ответ CharacterAction proposer")
        if value["proposal"] is None:
            return None
        proposal = validate_action_proposal(
            value["proposal"], allowed_source="self_initiated"
        )
        return proposal

    def _start_actions(self, event, effect_plan, audit, revision):
        proposals = effect_plan["actions"]
        audit["actions"] = {"planned": copy.deepcopy(proposals), "results": []}
        if not proposals:
            return []

        results = []
        self.progress(
            event["id"], "Действие утверждено", audit, revision
        )
        for proposal in proposals:
            record = self.store.reserve_action(event["id"], proposal)
            if record["created"]:
                result = self.actions.start(proposal, record["request_id"])
                persisted = self.store.record_action_result(
                    proposal["proposal_id"], result
                )
            else:
                persisted = record
            item = {
                "proposal_id": proposal["proposal_id"],
                "request_id": persisted["request_id"],
                "status": persisted["status"],
                "action_id": persisted.get("action_id"),
                "result": copy.deepcopy(persisted.get("result")),
            }
            results.append(item)
        audit["actions"]["results"] = copy.deepcopy(results)
        self.progress(
            event["id"], "Действие передано миру", audit, revision
        )
        return results

    def poll_actions(self):
        """Reconcile active jobs without creating dialogue turns or replaying starts."""
        changed = []
        for record in self.store.active_actions():
            action_id = record.get("action_id")
            if not action_id:
                continue
            observed = self.actions.poll(action_id)
            if (
                observed.get("status") == record.get("status")
                and observed == record.get("result")
            ):
                continue
            previous_status = record.get("status")
            updated = self.store.record_action_result(
                record["proposal_id"], observed
            )
            changed.append(updated)
            if updated["status"] != previous_status:
                self.events("action.updated", {
                    "proposal_id": record["proposal_id"],
                    "action_id": action_id,
                    "status": updated["status"],
                })
        return changed

    def cancel_active_actions(self, reason="story_boundary"):
        results = []
        for record in self.store.active_actions():
            action_id = record.get("action_id")
            if not action_id:
                continue
            request_id = (
                f"cancel.{reason}.{record['proposal_id']}"
            )[:120]
            outcome = self.actions.cancel(action_id, request_id)
            observed = {
                "action_id": action_id,
                "status": (
                    "cancelled"
                    if outcome.get("accepted") and not outcome.get("uncertain")
                    else record.get("status", "uncertain")
                ),
                "uncertain": bool(outcome.get("uncertain")),
                "result": copy.deepcopy(outcome),
            }
            updated = self.store.record_action_result(
                record["proposal_id"], observed
            )
            results.append(updated)
        return results

    def start_self_action(self, proposal, event_id=None):
        """Internal event/idle hook; proposal is validated before any side effect."""
        proposal = validate_action_proposal(
            proposal, allowed_source="self_initiated"
        )
        event_id = event_id or "initiative." + uuid.uuid4().hex
        record = self.store.reserve_action(event_id, proposal)
        if not record["created"]:
            return record
        result = self.actions.start(proposal, record["request_id"])
        return self.store.record_action_result(proposal["proposal_id"], result)

    def verbalize(self, parent, event, contract, facts, audit, revision):
        text = None
        correction = ""
        for attempt in range(2):
            self.progress(
                event["id"],
                "Юки подбирает слова" if not attempt else "Проверка формулировки",
                audit, revision,
            )
            record = {}
            try:
                response = self.backend.complete(
                    parent, "yuki", prompts.narration(facts, correction)
                )
                draft = parse_json(response["text"])
                record["draft"] = draft
                record["session_id"] = response["session_id"]
                candidate = validate_draft(draft, contract)

                verdict_response = self.backend.complete(
                    parent, "yuki-head", prompts.review(facts, draft)
                )
                verdict = parse_json(verdict_response["text"])
                record["review"] = verdict
                record["review_session"] = verdict_response["session_id"]
                if (
                    not isinstance(verdict, dict)
                    or set(verdict) != {"event_id", "ok", "reason"}
                    or verdict["event_id"] != event["id"]
                    or type(verdict["ok"]) is not bool
                    or not isinstance(verdict["reason"], str)
                ):
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
        world_observation = self.store.latest_world_observation()
        self.log(f"ход {event['id']}: intent={event['intent_id']}")
        audit = {
            "before": before,
            "world_observation": copy.deepcopy(world_observation),
            "model": self.backend.model,
            "rules": self.rules,
            "assessments": {},
            "draft_attempts": [],
            "actions": {"planned": [], "results": []},
        }
        try:
            parent = self.backend.create("GameTable turn " + event["id"])
            audit["parent_session"] = parent
            data = prompts.packet(
                event, before, self.rules, world_observation=world_observation
            )

            audit["assessments"] = self.appraise(parent, event, before, data)

            decision, context = decide_turn(
                before, event,
                audit["assessments"]["heart"]["report"],
                audit["assessments"]["head"]["report"],
                self.rules,
            )

            effect_plan = build_effect_plan(
                before, event, decision, context, self.rules,
                observation=world_observation,
            )

            after, state_audit = apply_effect_plan(
                before, event, decision, effect_plan, context, self.rules
            )
            contract = build_contract(
                event, after, decision, effect_plan, context, self.rules
            )
            calculations = calculation_audit(
                decision, effect_plan, context, state_audit
            )
            audit.update(
                after=after, contract=contract, calculations=calculations
            )
            disposition = decision["disposition"]
            self.log(
                f"ход {event['id']}: disposition={disposition}; "
                f"tone={decision['tone']}; actions={len(effect_plan['actions'])}"
            )
            self.progress(
                event["id"], "Решение и эффекты приняты", audit, before["revision"]
            )

            # Durable proposal/outbox first, then asynchronous world start.
            action_results = self._start_actions(
                event, effect_plan, audit, before["revision"]
            )

            # Narrator sees started/observed facts, never an inferred arrival.
            facts = prompts.narration_facts(
                data, before, contract, state_audit, after, action_results
            )
            audit["narration_facts"] = facts
            text = self.verbalize(
                parent, event, contract, facts, audit, before["revision"]
            )

            audit["reply"] = {
                "anchor": contract["anchor"],
                "text": text or "",
                "fallback": text is None,
                "disposition": disposition,
                "tone": decision["tone"],
            }
            audit["notice"] = (
                "Не удалось проверить свободную реплику. "
                "Показано только решение движка."
                if text is None else None
            )
            after["memories"][-1]["yuki"] = {
                "anchor": contract["anchor"], "text": text or ""
            }
            after["memories"][-1]["action_results"] = copy.deepcopy(action_results)

            # Character/dialogue commit is independent from already-started world action.
            self.store.finish(event["id"], before, after, audit)
            self.emit(
                "state.changed", event["id"], after["revision"],
                action_count=len(action_results),
            )
            self.emit("turn.completed", event["id"], after["revision"])
            if self.story is not None:
                self.story.on_turn_completed(event, audit)
        except Exception as exc:
            self.log(f"ход {event['id']}: {exc}", "ERROR")
            try:
                self.progress(
                    event["id"], "Ход остановлен", audit, before["revision"]
                )
            except Exception:
                pass
            self.store.fail(event["id"], str(exc))
            self.emit(
                "turn.failed", event["id"], self.store.state()["revision"],
                error=str(exc),
            )
        finally:
            self.backend.close_sessions()


def public_turn(turn):
    """Never expose in-flight/rejected drafts; action status is a durable fact."""
    item = {k: turn[k] for k in ("id", "event", "status", "stage", "error")}
    if turn["status"] == "done":
        result = turn["result"]
        item.update(
            reply=result["reply"],
            contract=result["contract"],
            actions=copy.deepcopy((result.get("actions") or {}).get("results", [])),
            notice=result.get("notice"),
        )
        item["delta"] = {
            k: round(
                result["after"]["stats"][k] - result["before"]["stats"][k], 2
            )
            for k in result["after"]["stats"]
        }
    return item
