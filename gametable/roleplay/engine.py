"""Pure, replayable game rules. Only this module computes state and decisions."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")
ACTIONS = ("respond", "warm", "playful", "boundary", "clarify", "rest", "work")
CATEGORIES = ("neutral", "praise", "criticism", "care", "affection", "promise", "conflict", "research")
SOCIAL = ("mood", "affection", "trust")
ANCHORS = {
    "respond": "Я отвечу по существу.",
    "warm": "Мне хочется ответить тебе тепло.",
    "playful": "Я позволю себе немного пошутить.",
    "boundary": "Сейчас я не готова с этим согласиться.",
    "clarify": "Мне нужно уточнение, прежде чем решать.",
    "rest": "Сейчас я выберу отдых.",
    "work": "Я займусь проверкой в лаборатории.",
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
            "memories": [], "recent_events": [], "last_action": None}


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
            or report["revision"] != state["revision"]
            or report["role"] != role):
        raise InvalidReport("Чужой или устаревший отчёт")
    if report["category"] not in CATEGORIES:
        raise InvalidReport("Неизвестный тип события")
    for key, names, lo, hi in (("impacts", SOCIAL, -2, 2), ("scores", ACTIONS, -1, 1)):
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


def reduce_turn(state, event, heart, head, rules):
    """No I/O, wall clock or random choices. Same inputs -> identical result."""
    if state["rules_hash"] != rules["hash"]:
        raise ValueError("Правила изменились: продолжение требует отдельного нового сохранения")
    heart = validate_report(heart, event, state, "heart")
    head = validate_report(head, event, state, "head")
    if event["activity"] not in rules["activities"]:
        raise ValueError("Неизвестное занятие")
    after = copy.deepcopy(state)
    stats = after["stats"]
    # Repeated input or repeated social category has diminishing, bounded effect.
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
        # Affection-prone characters warm more quickly, without raising trust for free.
        if name == "affection":
            delta *= 0.5 + rules["character"]["traits"]["attachment"] / 100
        stats[name] = clamp(stats[name] + delta)
        social_delta[name] = round(delta, 3)

    traits = rules["character"]["traits"]
    weight_h = max(0.25, min(0.75, 0.35 + traits["attachment"] / 500
                            + (stats["affection"] - 50) / 500 - traits["independence"] / 1000))
    bias = {"respond": 0.1, "warm": (stats["affection"] + stats["mood"] - 100) / 220,
            "playful": (stats["mood"] - traits["shyness"]) / 150,
            "boundary": (50 - stats["trust"]) / 160 + traits["independence"] / 500,
            "clarify": 0, "rest": stats["fatigue"] / 100 - 0.5,
            "work": traits["curiosity"] / 400 - stats["fatigue"] / 150}
    allowed = list(ACTIONS)
    if event["activity"] != "lab":
        allowed.remove("work")  # Chat never silently starts real laboratory operations.
    utilities = {a: round(weight_h * heart["scores"][a] + (1 - weight_h) * head["scores"][a]
                          + bias[a], 5) for a in allowed}
    forced = None
    if event["activity"] in ("rest", "sleep"):
        action = "rest"
        forced = "Выбран переход сцены с отдыхом"
    elif stats["fatigue"] >= 85 or stats["health"] <= 25:
        action = "rest"
        forced = "Нужен отдых: ресурсный предел"
    else:
        action = max(allowed, key=lambda a: utilities[a])
    activity = (event["activity"] if action == "rest" and event["activity"] in ("rest", "sleep")
                else "rest" if action == "rest" else "lab" if action == "work" else "chat")
    effect = rules["activities"][activity]
    old_fatigue = stats["fatigue"]
    stats["fatigue"] = clamp(stats["fatigue"] + effect["fatigue"])
    strain = 2 if activity == "lab" and old_fatigue >= 70 else 0
    stats["health"] = clamp(stats["health"] + effect["health"] - strain)
    if activity in ("rest", "sleep"):
        stats["mood"] = clamp(stats["mood"] + (55 - stats["mood"]) * (0.15 if activity == "rest" else 0.5))
    after["minutes"] += effect["minutes"]
    after["revision"] += 1
    after["last_action"] = action
    after["recent_events"] = (recent + [{"fingerprint": fingerprint, "category": category}])[-8:]
    # Keep observations, not generated claims about what the Director did.
    after["memories"] = (state["memories"] + [{"event_id": event["id"],
        "director": event["text"], "action": action, "activity": activity}])[-24:]
    conflict = round(sum(abs(heart["scores"][a] - head["scores"][a]) for a in ACTIONS) / len(ACTIONS), 3)
    contract = {"event_id": event["id"], "revision": after["revision"], "action": action,
                "anchor": ANCHORS[action], "activity": activity, "minutes": effect["minutes"],
                "stats": copy.deepcopy(stats), "conflict": conflict,
                "tone": {"warmth": round((stats["affection"] + stats["mood"]) / 2),
                         "shyness": traits["shyness"], "tiredness": stats["fatigue"]},
                "forced_reason": forced}
    audit = {"heart_weight": weight_h, "novelty": novelty, "social_delta": social_delta,
             "utilities": utilities, "forced_reason": forced, "conflict": conflict}
    return after, contract, audit


def validate_draft(value, contract):
    if not isinstance(value, dict) or set(value) != {"event_id", "action", "text"}:
        raise InvalidReport("Неверная структура реплики")
    if value["event_id"] != contract["event_id"] or value["action"] != contract["action"]:
        raise InvalidReport("Вербализатор подменил решение")
    if not isinstance(value["text"], str) or not 1 <= len(value["text"].strip()) <= 3500:
        raise InvalidReport("Пустая или слишком длинная реплика")
    return value["text"].strip()
