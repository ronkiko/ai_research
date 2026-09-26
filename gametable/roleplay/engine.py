"""Pure, replayable decisions, effect planning and world rules."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")
ACTION_RULES_PATH = Path(__file__).with_name("action_rules.json")
DISPOSITIONS = ("respond", "accept", "decline", "clarify")
TONES = ("neutral", "warm", "playful", "firm", "shy", "upset")
CATEGORIES = ("neutral", "praise", "criticism", "care", "affection", "promise", "conflict", "research")
SOCIAL = ("mood", "affection", "trust")
ANCHORS = {
    "respond": "Я отвечу по существу.",
    "accept": "Я согласна.",
    "decline": "Сейчас я не готова с этим согласиться.",
    "clarify": "Мне нужно уточнение, прежде чем решать.",
}


class InvalidReport(ValueError):
    pass


def load_rules():
    rules = json.loads(RULES_PATH.read_text())
    rules["hash"] = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()
    return rules


def initial_state(rules):
    return {"revision": 0, "rules_version": rules["version"], "rules_hash": rules["hash"],
            "stats": copy.deepcopy(rules["initial_stats"]), "minutes": 9 * 60,
            "scene_id": rules["initial_scene"], "memories": [], "recent_events": [],
            "last_decision": None}


def number(value, lo, hi):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and lo <= value <= hi)


def validate_report(report, event, state, role):
    if not isinstance(report, dict):
        raise InvalidReport("Отчёт должен быть JSON-объектом")
    required = {"event_id", "revision", "role", "category", "impacts", "scores", "evidence", "summary"}
    if set(report) != required:
        raise InvalidReport("Неверные поля отчёта")
    if (report["event_id"] != event["id"] or type(report["revision"]) is not int
            or report["revision"] != state["revision"] or report["role"] != role):
        raise InvalidReport("Чужой или устаревший отчёт")
    if report["category"] not in CATEGORIES:
        raise InvalidReport("Неизвестный тип события")
    for key, names, lo, hi in (("impacts", SOCIAL, -2, 2), ("scores", DISPOSITIONS, -1, 1)):
        values = report[key]
        if not isinstance(values, dict) or set(values) != set(names):
            raise InvalidReport(f"Неверные шкалы {key}")
        if not all(number(v, lo, hi) for v in values.values()):
            raise InvalidReport(f"Оценки {key} вне диапазона")
    if not isinstance(report["summary"], str) or not 1 <= len(report["summary"]) <= 1000:
        raise InvalidReport("Нужен краткий вывод")
    evidence = report["evidence"]
    if not isinstance(evidence, list) or not evidence or len(evidence) > 6:
        raise InvalidReport("Нужны цитаты исходного события")
    if not all(isinstance(q, str) and q.strip() and q in event["text"] for q in evidence):
        raise InvalidReport("Основания отсутствуют в исходном сообщении")
    return copy.deepcopy(report)


def clamp(value, lo=0, hi=100):
    return round(max(lo, min(hi, value)), 2)


def _tone_for(stats, disposition, traits):
    """Presentation only. Tone never participates in decision utility."""
    if disposition == "decline":
        return "firm"
    if stats["mood"] < 35:
        return "upset"
    if stats["mood"] >= 75 and traits["shyness"] < 60:
        return "playful"
    if stats["affection"] >= 50 and stats["mood"] >= 45:
        return "warm"
    if disposition == "clarify" and traits["shyness"] >= 65:
        return "shy"
    return "neutral"


def _decision_bias(intent_id, stats, traits):
    bias = {
        "respond": 0.1,
        "accept": 0.0,
        "decline": (50 - stats["trust"]) / 200 + traits["independence"] / 600,
        "clarify": 0.0,
    }
    if intent_id == "talk":
        bias["respond"] += 0.3
        bias["accept"] -= 0.6
    elif intent_id == "request_lab_work":
        bias["accept"] += traits["curiosity"] / 400 - stats["fatigue"] / 150
        bias["clarify"] += 0.05
    elif intent_id == "request_rest":
        bias["accept"] += stats["fatigue"] / 100 - 0.2
    elif intent_id == "request_sleep":
        bias["accept"] += stats["fatigue"] / 90 - 0.35
    return bias


def _social_assessment(state, event, heart, head, rules):
    fingerprint = hashlib.sha256(" ".join(event["text"].casefold().split()).encode()).hexdigest()
    category = heart["category"] if heart["category"] == head["category"] else "mixed"
    recent = state["recent_events"][-8:]
    repeats = sum(e["fingerprint"] == fingerprint or
                  (category not in ("mixed", "neutral", "research") and e["category"] == category)
                  for e in recent)
    novelty = 1 / (1 + repeats * 2)
    social_delta = {}
    for name in SOCIAL:
        weight = rules["heart_weights"][name]
        impact = weight * heart["impacts"][name] + (1 - weight) * head["impacts"][name]
        delta = rules["impact_steps"][name] * novelty * impact
        if name == "affection":
            delta *= 0.5 + rules["character"]["traits"]["attachment"] / 100
        social_delta[name] = round(delta, 3)
    projected = copy.deepcopy(state["stats"])
    for name, delta in social_delta.items():
        projected[name] = clamp(projected[name] + delta)
    return social_delta, projected, fingerprint, category, novelty


def _select_decision(projected_stats, event, heart, head, rules):
    traits = rules["character"]["traits"]
    weight_h = max(0.25, min(0.75, 0.35 + traits["attachment"] / 500
                            + (projected_stats["affection"] - 50) / 500
                            - traits["independence"] / 1000))
    bias = _decision_bias(event["intent_id"], projected_stats, traits)
    utilities = {d: round(weight_h * heart["scores"][d] + (1 - weight_h) * head["scores"][d]
                          + bias[d], 5) for d in DISPOSITIONS}
    forced_reason = None
    forced_effect = None
    if projected_stats["fatigue"] >= 85 or projected_stats["health"] <= 25:
        if event["intent_id"] in ("request_rest", "request_sleep"):
            disposition = "accept"
            forced_reason = "Ресурсный предел совпадает с просьбой об отдыхе"
        else:
            disposition = "decline"
            forced_effect = "rest"
            forced_reason = "Ресурсный предел: сначала отдых"
    else:
        disposition = max(DISPOSITIONS, key=lambda d: utilities[d])
    decision = {"disposition": disposition,
                "tone": _tone_for(projected_stats, disposition, traits)}
    conflict = round(sum(abs(heart["scores"][d] - head["scores"][d])
                         for d in DISPOSITIONS) / len(DISPOSITIONS), 3)
    return decision, {
        "heart_weight": weight_h,
        "utilities": utilities,
        "forced_reason": forced_reason,
        "forced_effect": forced_effect,
        "conflict": conflict,
    }


def _world_action(name, rules):
    try:
        action = rules["world_actions"][name]
    except KeyError as exc:
        raise ValueError(f"Неизвестный эффект состояния: {name}") from exc
    return {"type": name, "minutes": action["minutes"]}


def _load_action_rules():
    value = json.loads(ACTION_RULES_PATH.read_text())
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported action_rules schema")
    return value


ACTION_RULES = _load_action_rules()
KNOWN_WORLD_LOCATIONS = tuple(ACTION_RULES["locations"])


def legacy_location(state):
    """Temporary pre-cutover location hint. It never proves physical movement."""
    scene_id = state.get("scene_id")
    if scene_id == "laboratory.workstation":
        return "laboratory"
    return "hallway"


def observation_ref(observation, state):
    if isinstance(observation, dict) and observation.get("location_id") in KNOWN_WORLD_LOCATIONS:
        return {
            "source": "world",
            "world_epoch": observation.get("world_epoch"),
            "observed_tick": observation.get("observed_tick"),
            "location_id": observation["location_id"],
        }
    return {
        "source": "legacy_vn_hint",
        "world_epoch": None,
        "observed_tick": None,
        "location_id": legacy_location(state),
    }


def validate_action_proposal(value, *, allowed_source=None):
    required = {
        "proposal_id", "source", "action_type", "target_id",
        "rationale", "observation_ref", "scope",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Неверная структура CharacterActionProposal")
    if not isinstance(value["proposal_id"], str) or not value["proposal_id"]:
        raise ValueError("proposal_id обязателен")
    if value["source"] not in {"director_request", "self_initiated"}:
        raise ValueError("Неизвестный источник CharacterActionProposal")
    if allowed_source and value["source"] != allowed_source:
        raise ValueError("Источник CharacterActionProposal не разрешён")
    if value["action_type"] not in ACTION_RULES["actions"]:
        raise ValueError("Неизвестный тип CharacterActionProposal")
    if not isinstance(value["target_id"], str) or not value["target_id"]:
        raise ValueError("Semantic target обязателен")
    if not isinstance(value["rationale"], str) or not value["rationale"].strip():
        raise ValueError("CharacterActionProposal требует rationale")
    ref = value["observation_ref"]
    if not isinstance(ref, dict) or set(ref) != {
        "source", "world_epoch", "observed_tick", "location_id",
    }:
        raise ValueError("Неверная ссылка на наблюдение")
    if ref["location_id"] not in KNOWN_WORLD_LOCATIONS:
        raise ValueError("CharacterActionProposal ссылается на неизвестную location")
    scope = value["scope"]
    if not isinstance(scope, dict) or set(scope) != {"capability", "target_id"}:
        raise ValueError("Неверный action scope")
    if scope["capability"] != value["action_type"] or scope["target_id"] != value["target_id"]:
        raise ValueError("Action scope не совпадает с semantic target")
    allowed_targets = set(
        ACTION_RULES["actions"].get(value["action_type"], {}).get("targets", [])
    )
    if value["target_id"] not in allowed_targets:
        raise ValueError("Semantic target не разрешён action_rules")
    return copy.deepcopy(value)


def _proposal(event, state, action_type, target_id, observation, rationale):
    value = {
        "proposal_id": f"proposal.{event['id']}",
        "source": "director_request",
        "action_type": action_type,
        "target_id": target_id,
        "rationale": rationale,
        "observation_ref": observation_ref(observation, state),
        "scope": {"capability": action_type, "target_id": target_id},
    }
    return validate_action_proposal(value, allowed_source="director_request")


def build_director_action_proposal(state, event, decision, observation=None):
    """Translate an accepted Director request into a semantic proposal, never a movement fact."""
    if decision["disposition"] != "accept":
        return None
    current = observation_ref(observation, state)["location_id"]
    intent_id = event["intent_id"]
    policy = ACTION_RULES["director_intents"].get(intent_id)
    if policy is None:
        return None
    selected = policy.get(f"when_location_{current}", policy["default"])
    rationale = (
        "Директорская просьба принята; физический результат должен быть подтверждён "
        "semantic action lifecycle."
    )
    return _proposal(
        event, state, selected["action_type"], selected["target_id"],
        observation, rationale,
    )


def plan_effects(
    state, event, decision, social_delta, rules, forced_effect=None,
    *, observation=None, self_proposal=None,
):
    """Pure CharacterDecision -> state effects + approved semantic action proposal."""
    if state.get("scene_id") not in rules["scenes"]:
        raise ValueError("Неизвестная legacy VN scene")
    intent_id = event["intent_id"]
    if intent_id not in rules["intents"]:
        raise ValueError("Неизвестное намерение Директора")

    effects = [{"type": "social", "delta": copy.deepcopy(social_delta)}]
    actions = []

    if forced_effect:
        effects.append(_world_action(forced_effect, rules))
    elif intent_id == "request_rest" and decision["disposition"] == "accept":
        effects.append(_world_action("rest", rules))
    elif intent_id == "request_sleep" and decision["disposition"] == "accept":
        effects.append(_world_action("sleep", rules))
    else:
        # Conversation time/resource changes are character-state effects only.
        effects.append(_world_action("converse", rules))

    director_proposal = build_director_action_proposal(
        state, event, decision, observation
    )
    if director_proposal is not None:
        actions.append(director_proposal)

    if self_proposal is not None:
        proposal = validate_action_proposal(
            self_proposal, allowed_source="self_initiated"
        )
        if director_proposal is not None:
            raise ValueError("Один ход не может запускать две physical action")
        if decision["disposition"] == "accept":
            actions.append(proposal)

    if event.get("source") in {"world", "self_initiated"}:
        effects = []  # Observing or considering an action is not fictitious exercise/rest.
    duration = sum(effect.get("minutes", 0) for effect in effects)
    return {"state_effects": effects, "actions": actions, "duration": duration}


def reduce_character_state(
    state, event, decision, effect_plan, rules, fingerprint, category
):
    """Pure reducer for social/resource/narrative state. It never moves the body."""
    if state["rules_hash"] != rules["hash"]:
        raise ValueError("Правила изменились: продолжение требует отдельного нового сохранения")
    if state.get("scene_id") not in rules["scenes"]:
        raise ValueError("Неизвестная legacy VN scene")
    if not isinstance(effect_plan, dict) or set(effect_plan) != {
        "state_effects", "actions", "duration",
    }:
        raise ValueError("Неверный EffectPlan")

    after = copy.deepcopy(state)
    applied = []
    elapsed = 0
    for effect in effect_plan["state_effects"]:
        kind = effect.get("type")
        if kind == "social":
            delta = effect.get("delta")
            if not isinstance(delta, dict) or set(delta) != set(SOCIAL):
                raise ValueError("Неверный social effect")
            for name, value in delta.items():
                if not number(value, -20, 20):
                    raise ValueError("Неверное изменение social stat")
                after["stats"][name] = clamp(after["stats"][name] + value)
        elif kind in {"converse", "rest", "sleep"}:
            config = rules["world_actions"][kind]
            if effect.get("minutes") != config["minutes"]:
                raise ValueError("Неверная длительность state effect")
            old_fatigue = after["stats"]["fatigue"]
            after["stats"]["fatigue"] = clamp(old_fatigue + config["fatigue"])
            after["stats"]["health"] = clamp(
                after["stats"]["health"] + config["health"]
            )
            if kind in ("rest", "sleep"):
                factor = 0.15 if kind == "rest" else 0.5
                after["stats"]["mood"] = clamp(
                    after["stats"]["mood"]
                    + (55 - after["stats"]["mood"]) * factor
                )
            after["minutes"] += config["minutes"]
            elapsed += config["minutes"]
        else:
            raise ValueError(f"Неверный character-state effect: {kind}")
        applied.append(copy.deepcopy(effect))

    if elapsed != effect_plan["duration"]:
        raise ValueError("EffectPlan duration не совпадает с state effects")
    for proposal in effect_plan["actions"]:
        validate_action_proposal(proposal)

    after["revision"] += 1
    after["last_decision"] = copy.deepcopy(decision)
    recent = state["recent_events"][-8:]
    after["recent_events"] = (
        recent + [{"fingerprint": fingerprint, "category": category}]
    )[-8:]
    after["memories"] = (state["memories"] + [{
        "event_id": event["id"],
        "director": event["text"] if event.get("source", "director") == "director" else None,
        "source": event.get("source", "director"),
        "event_text": event["text"],
        "intent_id": event["intent_id"],
        "decision": copy.deepcopy(decision),
        "state_effects": copy.deepcopy(effect_plan["state_effects"]),
        "action_proposals": copy.deepcopy(effect_plan["actions"]),
        "action_results": [],
    }])[-24:]
    return after, {
        "applied": applied,
        "elapsed": elapsed,
        "legacy_scene_id": state["scene_id"],
        "physical_movement_applied": False,
    }


# Compatibility name for callers from earlier patches. It no longer owns world movement.
reduce_world = reduce_character_state


def decide_turn(state, event, heart, head, rules):
    """Pure DecisionEngine: validated appraisals -> CharacterDecision."""
    if state["rules_hash"] != rules["hash"]:
        raise ValueError("Правила изменились: продолжение требует отдельного нового сохранения")
    if state.get("scene_id") not in rules["scenes"]:
        raise ValueError("Неизвестная legacy VN scene")
    if event.get("intent_id") not in rules["intents"]:
        raise ValueError("Неизвестное намерение Директора")
    heart = validate_report(heart, event, state, "heart")
    head = validate_report(head, event, state, "head")
    social_delta, projected, fingerprint, category, novelty = _social_assessment(
        state, event, heart, head, rules
    )
    decision, selected = _select_decision(projected, event, heart, head, rules)
    return decision, {
        **selected,
        "social_delta": social_delta,
        "fingerprint": fingerprint,
        "category": category,
        "novelty": novelty,
    }


def build_effect_plan(
    state, event, decision, context, rules, *, observation=None, self_proposal=None
):
    """Pure EffectPlanner: character state is separate from semantic physical action."""
    return plan_effects(
        state, event, decision, context["social_delta"], rules,
        context["forced_effect"], observation=observation,
        self_proposal=self_proposal,
    )


def apply_effect_plan(state, event, decision, effect_plan, context, rules):
    """Pure CharacterStateReducer entrypoint; never changes physical location."""
    return reduce_character_state(
        state, event, decision, effect_plan, rules,
        context["fingerprint"], context["category"],
    )


def build_contract(event, after, decision, effect_plan, context, rules):
    return {
        "event_id": event["id"],
        "revision": after["revision"],
        "intent_id": event["intent_id"],
        "decision": copy.deepcopy(decision),
        "anchor": ANCHORS[decision["disposition"]],
        "effect_plan": copy.deepcopy(effect_plan),
        "minutes": effect_plan["duration"],
        "legacy_scene_id": after["scene_id"],
        "stats": copy.deepcopy(after["stats"]),
        "conflict": context["conflict"],
        "delivery": {
            "tone": decision["tone"],
            "warmth": round(
                (after["stats"]["affection"] + after["stats"]["mood"]) / 2
            ),
            "shyness": rules["character"]["traits"]["shyness"],
            "tiredness": after["stats"]["fatigue"],
        },
        "forced_reason": context["forced_reason"],
    }


def calculation_audit(decision, effect_plan, context, state_audit):
    return {
        "heart_weight": context["heart_weight"],
        "novelty": context["novelty"],
        "social_delta": copy.deepcopy(context["social_delta"]),
        "utilities": copy.deepcopy(context["utilities"]),
        "decision": copy.deepcopy(decision),
        "effect_plan": copy.deepcopy(effect_plan),
        "character_state": copy.deepcopy(state_audit),
        "forced_reason": context["forced_reason"],
        "conflict": context["conflict"],
    }


def reduce_turn(state, event, heart, head, rules, *, observation=None, self_proposal=None):
    """Compatibility pure orchestration used by deterministic unit tests."""
    decision, context = decide_turn(state, event, heart, head, rules)
    effect_plan = build_effect_plan(
        state, event, decision, context, rules,
        observation=observation, self_proposal=self_proposal,
    )
    after, state_audit = apply_effect_plan(
        state, event, decision, effect_plan, context, rules
    )
    contract = build_contract(event, after, decision, effect_plan, context, rules)
    return after, contract, calculation_audit(
        decision, effect_plan, context, state_audit
    )


def validate_draft(value, contract):
    required = {"event_id", "disposition", "text"}
    if not isinstance(value, dict) or set(value) != required:
        raise InvalidReport("Неверная структура реплики")
    disposition = contract["decision"]["disposition"]
    if value["event_id"] != contract["event_id"] or value["disposition"] != disposition:
        raise InvalidReport("Вербализатор подменил решение")
    if not isinstance(value["text"], str) or not 1 <= len(value["text"].strip()) <= 3500:
        raise InvalidReport("Пустая или слишком длинная реплика")
    return value["text"].strip()
