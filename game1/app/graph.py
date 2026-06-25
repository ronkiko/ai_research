from __future__ import annotations

from lab.engines.chip import analyze_chip

from .dto import SnapshotDto

_PLACEHOLDER_MESSAGE = "Graph inspector coming next"


def build_graph_payload(snapshot: SnapshotDto) -> dict[str, object]:
    graph: dict[str, object] = {
        "status": "placeholder",
        "message": _PLACEHOLDER_MESSAGE,
        "nodes": [],
        "edges": [],
        "truth_table": [],
    }

    analysis = analyze_chip(snapshot.model, snapshot.body)
    if analysis is not None:
        graph["network_role"] = analysis.output_role
        graph["target_role"] = analysis.target_role
        graph["match"] = analysis.real_network.solves_target
        graph["cmos_transistors"] = analysis.cmos.functional.transistors
        graph["truth_table"] = [
            {
                "x0": case.x0,
                "x1": case.x1,
                "network": case.output,
                "target": case.target,
            }
            for case in analysis.real_network.cases
        ]

    return {
        "snapshot": {
            "id": snapshot.id,
            "model": snapshot.model,
            "game": snapshot.game,
            "mode": snapshot.mode,
            "accuracy": snapshot.accuracy,
        },
        "graph": graph,
    }
