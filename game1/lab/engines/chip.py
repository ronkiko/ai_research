"""Движок `chip` — краткий булев разбор снапшота 2 → N → 1.

Главный результат строится только по реальному forward pass сети на входах
00/01/10/11. Hidden-bit approximation остаётся как компактная диагностика.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .base import ReportEngineInfo
from .common import (
    WEIGHT_EPS,
    STABLE_MARGIN,
    Parsed2Layer,
    activation_family,
    classify_mask,
    compute_margin,
    extract_2layer_weights,
    hidden_truth_table,
    mask_to_formula,
)
from .targets import target_for_game

_INPUT_CASES: tuple[tuple[int, int], ...] = ((0, 0), (0, 1), (1, 0), (1, 1))


@dataclass(frozen=True)
class SnapshotMeta:
    model: str
    game: str
    mode: str
    accuracy: str


@dataclass(frozen=True)
class RealNetworkCase:
    x0: int
    x1: int
    hidden_values: tuple[float, ...]
    logit: float
    output: int
    target: int | None


@dataclass(frozen=True)
class RealNetworkTruth:
    output_mask: tuple[int, int, int, int]
    target_mask: tuple[int, int, int, int] | None
    cases: list[RealNetworkCase]
    output_role: str
    target_role: str | None
    solves_target: bool | None


@dataclass(frozen=True)
class HiddenGate:
    index: int
    role: str
    mask: tuple[int, int, int, int]
    z_values: tuple[float, float, float, float]
    margin: float
    output_weight: float
    status: str


@dataclass(frozen=True)
class CmosCost:
    gates: list[str]
    transistors: int
    depth: int
    notes: list[str]


@dataclass(frozen=True)
class OutputCombiner:
    active_hidden: list[int]
    positive_hidden: list[int]
    negative_hidden: list[int]
    ignored_hidden: list[int]
    bias: float
    kind: str
    expression: str
    cost: CmosCost
    notes: list[str]


@dataclass(frozen=True)
class CmosBreakdown:
    functional: CmosCost
    raw_hidden: CmosCost | None


@dataclass(frozen=True)
class ChipAnalysis:
    model_key: str
    snapshot: SnapshotMeta
    hidden: list[HiddenGate]
    real_network: RealNetworkTruth
    output_mask: tuple[int, int, int, int]
    output_role: str
    target_mask: tuple[int, int, int, int] | None
    target_role: str | None
    result: str
    scheme: str
    cmos: CmosBreakdown
    output_combiner: OutputCombiner
    warnings: list[str]


def parse_snapshot_meta(body: str, model_key: str = "") -> SnapshotMeta:
    """Извлечь из markdown минимальные метаданные снапшота."""
    meta = {
        "model": model_key or "",
        "game": "",
        "mode": "",
        "accuracy": "",
    }
    field_map = {
        "Модель:": "model",
        "Игра:": "game",
        "Режим:": "mode",
        "Точность:": "accuracy",
    }
    for line in body.splitlines()[:20]:
        for key, field in field_map.items():
            if key not in line or "**" not in line:
                continue
            value = line.split("**")[-1].strip()
            if field == "accuracy":
                meta[field] = value.rstrip("%")
            elif field in ("model", "game"):
                meta[field] = value.split(" (")[0].strip()
            else:
                meta[field] = value
            break
    return SnapshotMeta(
        model=meta["model"] or model_key,
        game=meta["game"],
        mode=meta["mode"],
        accuracy=meta["accuracy"],
    )


def _hidden_statuses(hidden: list[HiddenGate]) -> list[HiddenGate]:
    """Пересчитать статусы скрытых нейронов для диагностического вида."""
    out: list[HiddenGate] = []
    seen_masks: set[tuple[int, int, int, int]] = set()
    for gate in hidden:
        if abs(gate.output_weight) < WEIGHT_EPS:
            status = "ignored_by_output"
        elif gate.mask in ((0, 0, 0, 0), (1, 1, 1, 1)):
            status = "constant"
        elif gate.mask in seen_masks:
            status = "duplicate"
        elif gate.margin < STABLE_MARGIN:
            status = "unstable"
        else:
            status = "active"

        if status not in ("ignored_by_output", "constant"):
            seen_masks.add(gate.mask)

        out.append(
            HiddenGate(
                index=gate.index,
                role=gate.role,
                mask=gate.mask,
                z_values=gate.z_values,
                margin=gate.margin,
                output_weight=gate.output_weight,
                status=status,
            )
        )
    return out


def _build_hidden_diagnostics(model_key: str, parsed: Parsed2Layer) -> list[HiddenGate]:
    """Черновой threshold-gate разбор hidden layer."""
    _family, binarize = activation_family(model_key)
    z_values, hidden_masks = hidden_truth_table(parsed.w0, parsed.b0, binarize)
    gates: list[HiddenGate] = []
    for index in range(parsed.hidden_n):
        role, _ = classify_mask(hidden_masks[index])
        gates.append(
            HiddenGate(
                index=index,
                role=role,
                mask=hidden_masks[index],
                z_values=z_values[index],
                margin=compute_margin(z_values[index]),
                output_weight=parsed.w1[index],
                status="",
            )
        )
    return _hidden_statuses(gates)


def _target_mask_for_game(game_key: str) -> tuple[int, int, int, int] | None:
    target = target_for_game(game_key)
    return target.mask if target is not None else None


def _hidden_value(model_key: str, z: float) -> float:
    if model_key == "mlp":
        return max(0.0, z)
    if model_key == "torch":
        return math.tanh(z)
    raise ValueError(f"unsupported model for chip analysis: {model_key}")


def evaluate_real_network(
    model_key: str,
    parsed: Parsed2Layer,
    game_key: str = "",
) -> RealNetworkTruth:
    """Честный forward pass сети 2 → N → 1 на 00/01/10/11."""
    target_mask = _target_mask_for_game(game_key)
    target_role = None
    if target_mask is not None:
        target_role = classify_mask(target_mask)[0]

    cases: list[RealNetworkCase] = []
    output_bits: list[int] = []
    for index, (x0, x1) in enumerate(_INPUT_CASES):
        hidden_values: list[float] = []
        for neuron in range(parsed.hidden_n):
            z = parsed.w0[0][neuron] * x0 + parsed.w0[1][neuron] * x1 + parsed.b0[neuron]
            hidden_values.append(_hidden_value(model_key, z))
        logit = sum(weight * value for weight, value in zip(parsed.w1, hidden_values)) + parsed.b2
        output = 1 if logit >= 0.0 else 0
        output_bits.append(output)
        cases.append(
            RealNetworkCase(
                x0=x0,
                x1=x1,
                hidden_values=tuple(hidden_values),
                logit=logit,
                output=output,
                target=target_mask[index] if target_mask is not None else None,
            )
        )

    output_mask = tuple(output_bits)  # type: ignore[assignment]
    output_role = classify_mask(output_mask)[0]
    solves_target = None
    if target_mask is not None:
        solves_target = output_mask == target_mask

    return RealNetworkTruth(
        output_mask=output_mask,
        target_mask=target_mask,
        cases=cases,
        output_role=output_role,
        target_role=target_role,
        solves_target=solves_target,
    )


def synthesize_expression(output_mask: tuple[int, int, int, int]) -> str:
    """Читаемая булева формула итоговой функции."""
    if output_mask == (1, 0, 0, 1):
        return "NOR(x₀, x₁) ∨ AND(x₀, x₁)"
    return mask_to_formula(output_mask)


def _dnf_terms(mask: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    return [case for case, bit in zip(_INPUT_CASES, mask) if bit]


def _dnf_cost(mask: tuple[int, int, int, int]) -> CmosCost:
    terms = _dnf_terms(mask)
    if not terms:
        return CmosCost([], 0, 0, ["Tie low."])
    if len(terms) == 4:
        return CmosCost([], 0, 0, ["Tie high."])

    gates: list[str] = []
    transistors = 0
    inverter_count = 0
    for x0, x1 in terms:
        gates.append("AND2")
        transistors += 6
        if x0 == 0:
            gates.append("INV")
            transistors += 2
            inverter_count += 1
        if x1 == 0:
            gates.append("INV")
            transistors += 2
            inverter_count += 1

    or_gates = max(0, len(terms) - 1)
    gates.extend(["OR2"] * or_gates)
    transistors += or_gates * 6
    depth = 1
    if inverter_count:
        depth = 2
    if or_gates:
        depth = max(depth, 3 if len(terms) > 1 else 2)

    return CmosCost(gates, transistors, depth, ["DNF synthesized from real truth table."])


def _functional_cmos(mask: tuple[int, int, int, int]) -> CmosCost:
    role, _ = classify_mask(mask)
    library: dict[str, CmosCost] = {
        "XNOR": CmosCost(["NOR2", "AND2", "OR2"], 16, 2, ["Canonical XNOR."]),
        "XOR": CmosCost(["INV", "INV", "AND2", "AND2", "OR2"], 22, 3, ["Canonical XOR."]),
        "NAND": CmosCost(["NAND2"], 4, 1, ["Direct NAND."]),
        "NOR": CmosCost(["NOR2"], 4, 1, ["Direct NOR."]),
        "AND": CmosCost(["AND2"], 6, 1, ["Direct AND."]),
        "OR": CmosCost(["OR2"], 6, 1, ["Direct OR."]),
        "PASS X0": CmosCost([], 0, 0, ["Direct wire from x₀."]),
        "PASS X1": CmosCost([], 0, 0, ["Direct wire from x₁."]),
        "NOT X0": CmosCost(["INV"], 2, 1, ["Direct inverter on x₀."]),
        "NOT X1": CmosCost(["INV"], 2, 1, ["Direct inverter on x₁."]),
        "X0 AND NOT X1": CmosCost(["INV", "AND2"], 8, 2, ["Direct mixed literal gate."]),
        "X1 AND NOT X0": CmosCost(["INV", "AND2"], 8, 2, ["Direct mixed literal gate."]),
        "X0 → X1": CmosCost(["INV", "OR2"], 8, 2, ["Direct implication gate."]),
        "X1 → X0": CmosCost(["INV", "OR2"], 8, 2, ["Direct implication gate."]),
        "ZERO": CmosCost([], 0, 0, ["Tie low."]),
        "ONE": CmosCost([], 0, 0, ["Tie high."]),
    }
    return library.get(role, _dnf_cost(mask))


def estimate_cmos(analysis: ChipAnalysis) -> CmosBreakdown:
    """Стоимость функциональной булевой схемы по реальному поведению сети."""
    return CmosBreakdown(
        functional=_functional_cmos(analysis.real_network.output_mask),
        raw_hidden=None,
    )


def _scheme_for_mask(mask: tuple[int, int, int, int]) -> str:
    role, _ = classify_mask(mask)
    schemes = {
        "XNOR": (
            "x₀ ─┬─────────────┐\n"
            "    │             ▼\n"
            "x₁ ─┴──► [NOR] ───┐\n"
            "                  ├──► [OR] ──► OUT\n"
            "x₀ ─┬──► [AND] ───┘\n"
            "x₁ ─┘"
        ),
        "XOR": (
            "x₀ ─┬──► [NOT] ──┐\n"
            "    │            ├──► [AND] ──┐\n"
            "x₁ ─┴────────────┘             │\n"
            "                               ├──► [OR] ──► OUT\n"
            "x₀ ─┴────────────┐             │\n"
            "    │            ├──► [AND] ──┘\n"
            "x₁ ─┬──► [NOT] ──┘"
        ),
        "NAND": "x₀ ─┐\n    ├──► [NAND] ──► OUT\nx₁ ─┘",
        "NOR": "x₀ ─┐\n    ├──► [NOR] ──► OUT\nx₁ ─┘",
        "AND": "x₀ ─┐\n    ├──► [AND] ──► OUT\nx₁ ─┘",
        "OR": "x₀ ─┐\n    ├──► [OR] ──► OUT\nx₁ ─┘",
        "PASS X0": "x₀ ─────────────► OUT",
        "PASS X1": "x₁ ─────────────► OUT",
        "NOT X0": "x₀ ───► [NOT] ───► OUT",
        "NOT X1": "x₁ ───► [NOT] ───► OUT",
        "ZERO": "0 ───────────────► OUT",
        "ONE": "1 ───────────────► OUT",
    }
    return schemes.get(role, "CUSTOM DNF\nsee proof table")


def _raw_neuron_lines(hidden: list[HiddenGate], limit: int = 8) -> list[str]:
    status_map = {
        "ignored_by_output": "ignored",
        "constant": "constant",
        "duplicate": "duplicate",
        "unstable": "unstable",
        "active": "active",
    }
    lines: list[str] = []
    for gate in hidden[:limit]:
        lines.append(
            f"h{gate.index}: {gate.role}, {status_map.get(gate.status, gate.status)}, "
            f"margin {gate.margin:.4f}"
        )
    if len(hidden) > limit:
        lines.append(f"... {len(hidden) - limit} more hidden neurons")
    lines.append("Real network output uses activation values, not hidden bits.")
    lines.append("Raw hidden approximation is not used for CMOS cost.")
    return lines


def _proof_lines(real_network: RealNetworkTruth) -> list[str]:
    lines: list[str] = []
    if real_network.target_mask is None:
        lines.append("Proof: network truth table only")
        lines.append("")
        lines.append("x₀ x₁ | network")
        for case in real_network.cases:
            lines.append(f"{case.x0}  {case.x1}  |    {case.output}")
        return lines

    lines.append("x₀ x₁ | target | network | ok")
    for case in real_network.cases:
        ok = "✓" if case.output == case.target else "✗"
        lines.append(
            f"{case.x0}  {case.x1}  |   {case.target}    |    {case.output}    | {ok}"
        )
    return lines


def analyze_chip(model_key: str, body: str) -> ChipAnalysis | None:
    """Построить анализ chip для поддерживаемой сети."""
    if model_key not in ("mlp", "torch"):
        return None

    parsed = extract_2layer_weights(model_key, body)
    if parsed is None:
        return None

    snapshot = parse_snapshot_meta(body, model_key=model_key)
    hidden = _build_hidden_diagnostics(model_key, parsed)
    real_network = evaluate_real_network(model_key, parsed, game_key=snapshot.game)

    warnings: list[str] = []
    if any(gate.status == "unstable" for gate in hidden):
        warnings.append("One or more hidden neurons sit too close to the threshold.")

    result = "NETWORK ONLY"
    if real_network.solves_target is True:
        result = "MATCH"
    elif real_network.solves_target is False:
        result = "FAIL"

    scheme = _scheme_for_mask(real_network.output_mask)
    placeholder_cost = CmosBreakdown(functional=CmosCost([], 0, 0, []), raw_hidden=None)
    analysis = ChipAnalysis(
        model_key=model_key,
        snapshot=snapshot,
        hidden=hidden,
        real_network=real_network,
        output_mask=real_network.output_mask,
        output_role=real_network.output_role,
        target_mask=real_network.target_mask,
        target_role=real_network.target_role,
        result=result,
        scheme=scheme,
        cmos=placeholder_cost,
        output_combiner=OutputCombiner(
            active_hidden=[gate.index for gate in hidden if gate.status == "active"],
            positive_hidden=[gate.index for gate in hidden if gate.output_weight > WEIGHT_EPS],
            negative_hidden=[gate.index for gate in hidden if gate.output_weight < -WEIGHT_EPS],
            ignored_hidden=[gate.index for gate in hidden if gate.status == "ignored_by_output"],
            bias=parsed.b2,
            kind="REAL NETWORK",
            expression=synthesize_expression(real_network.output_mask),
            cost=CmosCost([], 0, 0, []),
            notes=["Functional result comes from the real forward pass."],
        ),
        warnings=warnings,
    )
    return ChipAnalysis(
        model_key=analysis.model_key,
        snapshot=analysis.snapshot,
        hidden=analysis.hidden,
        real_network=analysis.real_network,
        output_mask=analysis.output_mask,
        output_role=analysis.output_role,
        target_mask=analysis.target_mask,
        target_role=analysis.target_role,
        result=analysis.result,
        scheme=analysis.scheme,
        cmos=estimate_cmos(analysis),
        output_combiner=analysis.output_combiner,
        warnings=analysis.warnings,
    )


_FIRST_SCREEN_LINES = 35
"""Сколько строк занимает чистый первый экран: заголовок, статус, схема,
CMOS, proof. Сырой raw neuron diagnostic должен начинаться ниже этого порога."""


def render_chip_report(analysis: ChipAnalysis) -> str:
    """Короткий отчёт для первого экрана chip.

    Первый экран = короткая булева схема: Game/Target/Network/Result, ASCII,
    CMOS COST, proof table. Raw neuron diagnostic уходит ниже разделителя,
    чтобы не превращать первый экран в лабораторный дамп.
    """
    main: list[str] = [
        "# CHIP",
        "",
        f"Game: {analysis.snapshot.game or 'unknown'}",
        f"Target: {analysis.target_role or 'unknown'}",
        f"Network: {analysis.output_role}",
        f"Result: {analysis.result}",
        "",
        "BOOLEAN CHIP SCHEME",
        "",
    ]
    main.extend(analysis.scheme.splitlines())
    main.append("")
    main.append(f"CMOS COST: {analysis.cmos.functional.transistors}T")
    main.append("")
    main.append("PROOF")
    main.append("")
    main.extend(_proof_lines(analysis.real_network))
    main.append("")
    main.append("DEBUG: press 1/2/3 to switch engines; raw neuron diagnostic below")

    # Дополняем чистый блок до высоты первого экрана, чтобы разделитель raw
    # diagnostic гарантированно ушёл ниже fold.
    if len(main) < _FIRST_SCREEN_LINES:
        main.extend([""] * (_FIRST_SCREEN_LINES - len(main)))

    lines: list[str] = list(main)
    lines.append("--- RAW NEURON DIAGNOSTIC ---")
    lines.append("")
    lines.extend(_raw_neuron_lines(analysis.hidden))
    if analysis.warnings:
        lines.append("")
        lines.extend(f"Warning: {warning}" for warning in analysis.warnings)
    lines.append("")
    lines.append("(Esc — назад)")
    return "\n".join(lines)


def _unsupported_chip_report(model_key: str, body: str) -> str:
    del body
    lines = [
        "# CHIP",
        "",
        "chip engine supports 2 → N → 1 snapshots in this version.",
        "",
        f"Current model: {model_key}",
        "Reason: no hidden layer weights were found, or the architecture is unsupported.",
        "",
        "Use `forensic` / `prune`, or load an `mlp` / `torch` snapshot.",
        "",
        "(Esc — назад)",
    ]
    return "\n".join(lines)


class ChipEngine:
    info = ReportEngineInfo(
        key="chip",
        hotkey="1",
        title="chip",
        summary="chip / CMOS analysis: real truth table + boolean scheme",
    )

    @staticmethod
    def render(model_key: str, snapshot_body: str) -> str:
        analysis = analyze_chip(model_key, snapshot_body)
        if analysis is None:
            return _unsupported_chip_report(model_key, snapshot_body)
        return render_chip_report(analysis)
