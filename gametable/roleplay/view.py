"""Pure projection of social/dialogue state; graphics owns world presentation."""
from __future__ import annotations
import copy

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

def available_intent_ids(state, rules):
    scene_id = state.get("scene_id")
    scene = rules["scenes"].get(scene_id)
    if not scene:
        raise ValueError("Неизвестная сцена")
    return tuple(scene["affordances"])

def project_view(
    state, rules, busy=False, stage=None, *,
    frame_ref=None, presentation_mode="vn_dialogue",
):
    """Project only VN/social state. World image is referenced, never constructed here."""
    stats = copy.deepcopy(state["stats"])
    hour = (state["minutes"] % 1440) // 60
    affordances = []
    for intent_id in available_intent_ids(state, rules):
        intent = rules["intents"][intent_id]
        affordances.append({
            "intent_id": intent_id,
            "label": intent["label"],
            "kind": intent["ui_kind"],
            "default_text": intent.get("default_text", ""),
        })
    return {
        "revision": state["revision"],
        "minutes": state["minutes"],
        "time": {
            "day": 1 + state["minutes"] // 1440,
            "hour": hour,
            "minute": state["minutes"] % 60,
            "night": hour >= 20 or hour < 7,
        },
        "stats": stats,
        "relationship_label": relationship_label(stats),
        "affordances": affordances,
        "busy": bool(busy),
        "stage": stage,
        "presentation_mode": presentation_mode,
        "frame_ref": copy.deepcopy(frame_ref),
    }
