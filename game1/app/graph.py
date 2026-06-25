from __future__ import annotations

from lab.engines.chip import ChipAnalysis, analyze_chip
from lab.engines.common import EPS, extract_2layer_weights, parse_weights

from .dto import SnapshotDto

_INPUT_CASES: tuple[tuple[str, int, int], ...] = (
    ("00", 0, 0),
    ("01", 0, 1),
    ("10", 1, 0),
    ("11", 1, 1),
)

_UNSUPPORTED_REASON = "only mlp 2→N→1 snapshots are supported in this patch."


def build_graph_payload(snapshot: SnapshotDto) -> dict[str, object]:
    analysis = analyze_chip(snapshot.model, snapshot.body)
    if snapshot.model != "mlp":
        graph = _unsupported(snapshot, _UNSUPPORTED_REASON)
        _apply_chip_summary(graph, analysis)
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

    if analysis is None:
        graph = _unsupported(snapshot, _UNSUPPORTED_REASON)
    else:
        graph = _build_mlp_graph(snapshot, analysis)

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


def _build_mlp_graph(snapshot: SnapshotDto, analysis: ChipAnalysis) -> dict[str, object]:
    raw_weights = parse_weights(snapshot.body)
    parsed = extract_2layer_weights(snapshot.model, snapshot.body)
    if not raw_weights or parsed is None:
        return _unsupported(snapshot, _UNSUPPORTED_REASON)

    hidden_by_index = {gate.index: gate for gate in analysis.hidden}
    cases: list[dict[str, object]] = []
    hidden_activity: dict[int, list[bool]] = {index: [] for index in range(parsed.hidden_n)}

    for case_id, x0, x1 in _INPUT_CASES:
        hidden_case: dict[str, object] = {}
        hidden_values: list[float] = []
        for neuron in range(parsed.hidden_n):
            z_value = parsed.w0[0][neuron] * x0 + parsed.w0[1][neuron] * x1 + parsed.b0[neuron]
            activation = max(0.0, z_value)
            active = activation > EPS
            hidden_values.append(activation)
            hidden_activity[neuron].append(active)
            hidden_case[f"h{neuron}"] = {
                "z": z_value,
                "activation": activation,
                "active": active,
            }

        logit = sum(weight * value for weight, value in zip(parsed.w1, hidden_values)) + parsed.b2
        network = 1 if logit >= 0.0 else 0
        target = next(
            (
                case.target
                for case in analysis.real_network.cases
                if case.x0 == x0 and case.x1 == x1
            ),
            None,
        )
        cases.append(
            {
                "id": case_id,
                "x0": x0,
                "x1": x1,
                "target": target,
                "network": network,
                "logit": logit,
                "hidden": hidden_case,
            }
        )

    hidden_nodes: list[dict[str, object]] = []
    for neuron in range(parsed.hidden_n):
        gate = hidden_by_index.get(neuron)
        hidden_nodes.append(
            {
                "id": f"h{neuron}",
                "index": neuron,
                "label": f"h{neuron}",
                "bias": parsed.b0[neuron],
                "role": gate.role if gate is not None else "unknown",
                "output_weight": parsed.w1[neuron],
                "dead": not any(hidden_activity[neuron]),
            }
        )

    edges: list[dict[str, object]] = []
    for neuron in range(parsed.hidden_n):
        edges.append(
            {
                "from": "x0",
                "to": f"h{neuron}",
                "weight": parsed.w0[0][neuron],
            }
        )
        edges.append(
            {
                "from": "x1",
                "to": f"h{neuron}",
                "weight": parsed.w0[1][neuron],
            }
        )
        edges.append(
            {
                "from": f"h{neuron}",
                "to": "out",
                "weight": parsed.w1[neuron],
            }
        )

    graph: dict[str, object] = {
        "status": "ok",
        "message": "",
        "model_type": "mlp",
        "activation": "relu",
        "target_role": analysis.target_role or "unknown",
        "network_role": analysis.output_role or "unknown",
        "match": analysis.real_network.solves_target,
        "cmos_transistors": analysis.cmos.functional.transistors,
        "inputs": [
            {"id": "x0", "label": "x0"},
            {"id": "x1", "label": "x1"},
        ],
        "hidden": hidden_nodes,
        "output": {
            "id": "out",
            "label": "out",
            "bias": parsed.b2,
        },
        "edges": edges,
        "cases": cases,
    }
    return graph


def _unsupported(snapshot: SnapshotDto, message: str) -> dict[str, object]:
    return {
        "status": "unsupported",
        "message": f"Graph unsupported for this snapshot. Reason: {message}",
        "model_type": snapshot.model or "unknown",
        "activation": "unknown",
        "target_role": "unknown",
        "network_role": "unknown",
        "match": None,
        "cmos_transistors": None,
        "inputs": [],
        "hidden": [],
        "output": {},
        "edges": [],
        "cases": [],
    }


def _apply_chip_summary(graph: dict[str, object], analysis: ChipAnalysis | None) -> None:
    if analysis is None:
        return
    graph["target_role"] = analysis.target_role or "unknown"
    graph["network_role"] = analysis.output_role or "unknown"
    graph["match"] = analysis.real_network.solves_target
    graph["cmos_transistors"] = analysis.cmos.functional.transistors
