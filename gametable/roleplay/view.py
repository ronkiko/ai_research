"""Pure projection of social/dialogue state; graphics owns world presentation."""
from __future__ import annotations
import copy

WORLD_AFFORDANCES = {
    "hallway": ("talk", "request_lab_work", "request_rest", "request_sleep"),
    "laboratory": ("talk", "request_lab_work", "request_leave_lab", "request_rest", "request_sleep"),
    "training/flat_run": ("talk", "request_leave_lab", "request_rest", "request_sleep"),
}

def relationship_label(stats):
    if stats["affection"] >= 65 and stats["trust"] >= 60:
        return "Близость и доверие"
    if stats["affection"] >= 65:
        return "Тянется к тебе, но сомневается"
    if stats["trust"] >= 60:
        return "Полагается на тебя"
    if stats["trust"] < 20:
        return "Осторожность"
    return "Узнаёте друг друга"

def available_intent_ids(state, rules, world_observation=None):
    if isinstance(world_observation, dict):
        location = world_observation.get("location_id")
        if location in WORLD_AFFORDANCES:
            return WORLD_AFFORDANCES[location]
    scene_id = state.get("scene_id")
    scene = rules["scenes"].get(scene_id)
    if not scene:
        raise ValueError("Неизвестная legacy VN scene")
    return tuple(scene["affordances"])

def project_view(
    state, rules, busy=False, stage=None, *,
    frame_ref=None, presentation_mode="vn_dialogue", world_observation=None,
):
    stats = copy.deepcopy(state["stats"])
    hour = (state["minutes"] % 1440) // 60
    affordances = []
    for intent_id in available_intent_ids(state, rules, world_observation):
        intent = rules["intents"][intent_id]
        affordances.append({
            "intent_id": intent_id, "label": intent["label"],
            "kind": intent["ui_kind"], "default_text": intent.get("default_text", ""),
        })
    return {
        "revision": state["revision"],
        "minutes": state["minutes"],
        "time": {
            "day": 1 + state["minutes"] // 1440, "hour": hour,
            "minute": state["minutes"] % 60, "night": hour >= 20 or hour < 7,
        },
        "stats": stats,
        "relationship_label": relationship_label(stats),
        "affordances": affordances,
        "busy": bool(busy),
        "stage": stage,
        "presentation_mode": presentation_mode,
        "frame_ref": copy.deepcopy(frame_ref),
        "world_observation": copy.deepcopy(world_observation),
    }
