"""Pure, replayable decisions, effect planning and world rules."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")
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


def _transition(rules, from_scene, to_scene):
    matches = [item for item in rules["transitions"]
               if item["from"] == from_scene and item["to"] == to_scene]
    if len(matches) != 1:
        raise ValueError(f"Переход сцены запрещён: {from_scene} -> {to_scene}")
    return matches[0]


def _world_action(name, rules):
    try:
        action = rules["world_actions"][name]
    except KeyError as exc:
        raise ValueError(f"Неизвестный эффект мира: {name}") from exc
    return {"type": name, "minutes": action["minutes"]}


def plan_effects(state, event, decision, social_delta, rules, forced_effect=None):
    """Pure CharacterDecision -> EffectPlan. Director intent never mutates world."""
    scene_id = state.get("scene_id")
    if scene_id not in rules["scenes"]:
        raise ValueError("Неизвестная сцена")
    intent_id = event["intent_id"]
    if intent_id not in rules["intents"]:
        raise ValueError("Неизвестное намерение Директора")

    world = [{"type": "social", "delta": copy.deepcopy(social_delta)}]
    external = []

    if forced_effect:
        world.append(_world_action(forced_effect, rules))
    elif intent_id == "talk" or decision["disposition"] != "accept":
        world.append(_world_action("converse", rules))
    elif intent_id == "request_rest":
        world.append(_world_action("rest", rules))
    elif intent_id == "request_sleep":
        world.append(_world_action("sleep", rules))
    elif intent_id == "request_lab_work":
        workstation = "laboratory.workstation"
        if scene_id != workstation:
            transition = _transition(rules, scene_id, workstation)
            world.append({"type": "move", "from": scene_id, "to": workstation,
                          "minutes": transition["minutes"]})
        world.append(_world_action("lab_work", rules))
        external.append({"type": "laboratory_step"})
    elif intent_id == "request_leave_lab":
        transition = _transition(rules, scene_id, "hallway")
        world.append({"type": "move", "from": scene_id, "to": "hallway",
                      "minutes": transition["minutes"]})
    else:
        raise ValueError("Для намерения не задан план эффектов")

    duration = sum(effect.get("minutes", 0) for effect in world)
    return {"world_effects": world, "external_effects": external, "duration": duration}


def reduce_world(state, event, decision, effect_plan, rules, fingerprint, category):
    """Pure WorldReducer. This is the only function that changes scene/time/stats."""
    if state["rules_hash"] != rules["hash"]:
        raise ValueError("Правила изменились: продолжение требует отдельного нового сохранения")
    if state.get("scene_id") not in rules["scenes"]:
        raise ValueError("Неизвестная сцена")
    if not isinstance(effect_plan, dict) or set(effect_plan) != {
            "world_effects", "external_effects", "duration"}:
        raise ValueError("Неверный EffectPlan")

    after = copy.deepcopy(state)
    applied = []
    elapsed = 0
    saw_lab_work = False

    for effect in effect_plan["world_effects"]:
        kind = effect.get("type")
        if kind == "social":
            delta = effect.get("delta")
            if not isinstance(delta, dict) or set(delta) != set(SOCIAL):
                raise ValueError("Неверный social effect")
            for name, value in delta.items():
                if not number(value, -20, 20):
                    raise ValueError("Неверное изменение social stat")
                after["stats"][name] = clamp(after["stats"][name] + value)
        elif kind == "move":
            if effect.get("from") != after["scene_id"]:
                raise ValueError("Move начинается не из текущей сцены")
            transition = _transition(rules, after["scene_id"], effect.get("to"))
            if effect.get("minutes") != transition["minutes"]:
                raise ValueError("Неверная длительность перехода")
            after["scene_id"] = effect["to"]
            after["minutes"] += transition["minutes"]
            elapsed += transition["minutes"]
        elif kind in rules["world_actions"]:
            config = rules["world_actions"][kind]
            if effect.get("minutes") != config["minutes"]:
                raise ValueError("Неверная длительность world effect")
            required = config.get("required_scene")
            if required and after["scene_id"] != required:
                raise ValueError(f"{kind} недоступен в сцене {after['scene_id']}")
            old_fatigue = after["stats"]["fatigue"]
            after["stats"]["fatigue"] = clamp(old_fatigue + config["fatigue"])
            strain = 2 if kind == "lab_work" and old_fatigue >= 70 else 0
            after["stats"]["health"] = clamp(after["stats"]["health"] + config["health"] - strain)
            if kind in ("rest", "sleep"):
                factor = 0.15 if kind == "rest" else 0.5
                after["stats"]["mood"] = clamp(
                    after["stats"]["mood"] + (55 - after["stats"]["mood"]) * factor)
            after["minutes"] += config["minutes"]
            elapsed += config["minutes"]
            saw_lab_work = saw_lab_work or kind == "lab_work"
        else:
            raise ValueError(f"Неизвестный world effect: {kind}")
        applied.append(copy.deepcopy(effect))

    if elapsed != effect_plan["duration"]:
        raise ValueError("EffectPlan duration не совпадает с эффектами")
    for effect in effect_plan["external_effects"]:
        if effect != {"type": "laboratory_step"}:
            raise ValueError("Неизвестный внешний effect")
        if not saw_lab_work or after["scene_id"] != "laboratory.workstation":
            raise ValueError("Лабораторный внешний effect не подкреплён world effect")

    after["revision"] += 1
    after["last_decision"] = copy.deepcopy(decision)
    recent = state["recent_events"][-8:]
    after["recent_events"] = (recent + [{"fingerprint": fingerprint, "category": category}])[-8:]
    after["memories"] = (state["memories"] + [{
        "event_id": event["id"],
        "director": event["text"],
        "intent_id": event["intent_id"],
        "decision": copy.deepcopy(decision),
        "world_effects": copy.deepcopy(effect_plan["world_effects"]),
        "external_effects": copy.deepcopy(effect_plan["external_effects"]),
    }])[-24:]
    return after, {"applied": applied, "elapsed": elapsed,
                   "scene_before": state["scene_id"], "scene_after": after["scene_id"]}


def decide_turn(state, event, heart, head, rules):
    """Pure DecisionEngine: validated appraisals -> CharacterDecision."""
    if state["rules_hash"] != rules["hash"]:
        raise ValueError("Правила изменились: продолжение требует отдельного нового сохранения")
    if state.get("scene_id") not in rules["scenes"]:
        raise ValueError("Неизвестная сцена")
    if event.get("intent_id") not in rules["intents"]:
        raise ValueError("Неизвестное намерение Директора")
    heart = validate_report(heart, event, state, "heart")
    head = validate_report(head, event, state, "head")
    social_delta, projected, fingerprint, category, novelty = _social_assessment(
        state, event, heart, head, rules)
    decision, selected = _select_decision(projected, event, heart, head, rules)
    return decision, {**selected, "social_delta": social_delta, "fingerprint": fingerprint,
                      "category": category, "novelty": novelty}


def build_effect_plan(state, event, decision, context, rules):
    """Pure EffectPlanner."""
    return plan_effects(state, event, decision, context["social_delta"], rules,
                        context["forced_effect"])


def apply_effect_plan(state, event, decision, effect_plan, context, rules):
    """Pure WorldReducer entrypoint."""
    return reduce_world(state, event, decision, effect_plan, rules,
                        context["fingerprint"], context["category"])


def build_contract(event, after, decision, effect_plan, context, rules):
    return {
        "event_id": event["id"], "revision": after["revision"], "intent_id": event["intent_id"],
        "decision": copy.deepcopy(decision), "anchor": ANCHORS[decision["disposition"]],
        "effect_plan": copy.deepcopy(effect_plan), "minutes": effect_plan["duration"],
        "scene_id": after["scene_id"], "stats": copy.deepcopy(after["stats"]),
        "conflict": context["conflict"],
        "delivery": {"tone": decision["tone"],
                     "warmth": round((after["stats"]["affection"] + after["stats"]["mood"]) / 2),
                     "shyness": rules["character"]["traits"]["shyness"],
                     "tiredness": after["stats"]["fatigue"]},
        "forced_reason": context["forced_reason"],
    }


def calculation_audit(decision, effect_plan, context, world_audit):
    return {"heart_weight": context["heart_weight"], "novelty": context["novelty"],
            "social_delta": copy.deepcopy(context["social_delta"]),
            "utilities": copy.deepcopy(context["utilities"]),
            "decision": copy.deepcopy(decision), "effect_plan": copy.deepcopy(effect_plan),
            "world": copy.deepcopy(world_audit), "forced_reason": context["forced_reason"],
            "conflict": context["conflict"]}


def reduce_turn(state, event, heart, head, rules):
    """Compatibility pure orchestration; Runtime executes these stages explicitly."""
    decision, context = decide_turn(state, event, heart, head, rules)
    effect_plan = build_effect_plan(state, event, decision, context, rules)
    after, world_audit = apply_effect_plan(state, event, decision, effect_plan, context, rules)
    contract = build_contract(event, after, decision, effect_plan, context, rules)
    return after, contract, calculation_audit(decision, effect_plan, context, world_audit)

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
