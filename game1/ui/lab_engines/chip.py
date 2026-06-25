"""Движок `chip` — лабораторный разбор весов как булевой схемы / CMOS-чипа.

Чистая функция: на вход `model_key` + `snapshot_body`, на выход — markdown
отчёт или `None`, если архитектура не поддерживается.

См. документацию: `game1/docs/chip-analysis-engine.md`.
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
    join_formula_terms,
    mask_to_formula,
    mask_to_truth,
)

# -----------------------------------------------------------------------------
# Структуры данных анализа
# -----------------------------------------------------------------------------


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
class ChipAnalysis:
    model_key: str
    hidden: list[HiddenGate]
    output_mask: tuple[int, int, int, int]
    output_role: str
    expression: str
    cmos: CmosCost
    warnings: list[str]


# -----------------------------------------------------------------------------
# Статусы скрытых вентилей
# -----------------------------------------------------------------------------

def _hidden_statuses(
    hidden: list[HiddenGate],
) -> list[HiddenGate]:
    """Пересчитать статусы с учётом дубликатов.

    Приоритет:
      1. ignored_by_output — выходной вес ≈ 0;
      2. constant — маска 0000 / 1111;
      3. duplicate — такая же маска уже встречалась раньше (среди значимых);
      4. unstable — малый margin;
      5. active.
    """
    out: list[HiddenGate] = []
    seen_masks: set[tuple[int, int, int, int]] = set()
    for g in hidden:
        if abs(g.output_weight) < WEIGHT_EPS:
            status = "ignored_by_output"
        elif g.mask in ((0, 0, 0, 0), (1, 1, 1, 1)):
            status = "constant"
        elif g.mask in seen_masks:
            status = "duplicate"
        elif g.margin < STABLE_MARGIN:
            status = "unstable"
        else:
            status = "active"

        if status not in ("ignored_by_output", "constant"):
            seen_masks.add(g.mask)

        out.append(
            HiddenGate(
                index=g.index,
                role=g.role,
                mask=g.mask,
                z_values=g.z_values,
                margin=g.margin,
                output_weight=g.output_weight,
                status=status,
            )
        )
    return out


# -----------------------------------------------------------------------------
# Output combiner
# -----------------------------------------------------------------------------

def compute_output_truth_table(
    w1: list[float],
    b2: float,
    hidden_masks: list[tuple[int, int, int, int]],
) -> tuple[tuple[int, int, int, int], list[float]]:
    """Вычислить truth table выхода по реальным скрытым битам.

    net = Σ W2[k] * hidden_bit[k] + b2; output_bit = 1 if net >= 0 else 0.
    Возвращает (output_mask, nets_per_case).
    """
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    nets: list[float] = []
    outs: list[int] = []
    for case in cases:
        hidden_bits = [mask_to_truth(m)[case] for m in hidden_masks]
        net = sum(w * b for w, b in zip(w1, hidden_bits)) + b2
        nets.append(net)
        outs.append(1 if net >= 0 else 0)
    return tuple(outs), nets  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# Извлечённая булева формула выхода
# -----------------------------------------------------------------------------

def synthesize_expression(output_mask: tuple[int, int, int, int]) -> str:
    """Построить читаемую булеву формулу выхода.

    XNOR — предпочитаемая человекочитаемая форма;
    известные роли — каноническая формула;
    неизвестная маска — ДНФ по минтермам.
    """
    role, _ = classify_mask(output_mask)

    if output_mask == (1, 0, 0, 1):
        return "NOR(x₀, x₁) ∨ AND(x₀, x₁)"

    return mask_to_formula(output_mask)


# -----------------------------------------------------------------------------
# CMOS-оценка
# -----------------------------------------------------------------------------

# базовая библиотека: (стоимость в транзисторах, depth вентиля)
_GATE_COST: dict[str, tuple[int, int]] = {
    "INV": (2, 1),
    "NAND2": (4, 1),
    "NOR2": (4, 1),
    "AND2": (6, 1),
    "OR2": (6, 1),
}

# роль скрытого нейрона → вентиль для CMOS-сети
_ROLE_GATE: dict[str, str | None] = {
    "ZERO": None,
    "ONE": None,
    "OR": "OR2",
    "AND": "AND2",
    "NAND": "NAND2",
    "NOR": "NOR2",
    "XOR": None,      # кастомная декомпозиция, если встретится
    "XNOR": None,     # кастомная декомпозиция, если встретится
    "PASS X0": None,  # провод
    "PASS X1": None,
    "NOT X0": "INV",
    "NOT X1": "INV",
    "X0 AND NOT X1": "AND2",  # инвертор учитывается дополнительно
    "X1 AND NOT X0": "AND2",
    "X0 → X1": "OR2",         # (!x0 || x1)
    "X1 → X0": "OR2",
    "CUSTOM": None,
}


def _hidden_gate_cmos(gate: HiddenGate) -> tuple[list[str], int, int, list[str]]:
    """Стоимость одного скрытого вентиля.

    Возвращает (gates, transistors, depth, notes).
    """
    role = gate.role
    if gate.status in ("ignored_by_output", "constant"):
        return [], 0, 0, []

    gate_name = _ROLE_GATE.get(role)
    if gate_name is None:
        if role in ("XOR", "XNOR", "CUSTOM"):
            # Кастомная декомпозиция по ДНФ на 2 входа.
            terms = _dnf_terms(gate.mask)
            cost = _dnf_cost(terms)
            notes = [f"h{gate.index} ({role}) decomposed to DNF: {cost['gates']} = {cost['transistors']}T"]
            return cost["gates"], cost["transistors"], cost["depth"], notes
        return [], 0, 0, []

    cost, depth = _GATE_COST[gate_name]
    extra = 0
    extra_gates: list[str] = []
    notes: list[str] = []
    if role in ("X0 AND NOT X1", "X1 AND NOT X0"):
        extra = _GATE_COST["INV"][0]
        extra_gates = ["INV"]
        notes.append(f"h{gate.index} needs one INV for the negated input")
    if role in ("X0 → X1", "X1 → X0"):
        extra = _GATE_COST["INV"][0]
        extra_gates = ["INV"]
        notes.append(f"h{gate.index} implication needs one INV")

    gates = [gate_name] + extra_gates
    return gates, cost + extra, max(depth, _GATE_COST["INV"][1] if extra else 0), notes


def _dnf_terms(mask: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Минтермы ДНФ как (x0_literal, x1_literal): 0=¬, 1=x, None=нет."""
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    terms: list[tuple[int | None, int | None]] = []
    for (xi, xj), bit in zip(cases, mask):
        if bit:
            terms.append((xi, xj))
    return terms  # type: ignore[return-value]


def _dnf_cost(terms: list[tuple[int | None, int | None]]) -> dict:
    """Стоимость ДНФ по минтермам на двух входах."""
    if not terms:
        return {"gates": [], "transistors": 0, "depth": 0}
    if len(terms) == 4:
        return {"gates": [], "transistors": 0, "depth": 0}

    and_gates = 0
    inv_gates = 0
    for xi, xj in terms:
        and_gates += 1
        if xi == 0:
            inv_gates += 1
        if xj == 0:
            inv_gates += 1

    or_gates = max(0, len(terms) - 1)
    transistors = (
        and_gates * _GATE_COST["AND2"][0]
        + inv_gates * _GATE_COST["INV"][0]
        + or_gates * _GATE_COST["OR2"][0]
    )
    depth = 2 if or_gates else 1
    if inv_gates:
        depth = max(depth, 2)

    gates = []
    if and_gates:
        gates.extend(["AND2"] * and_gates)
    if inv_gates:
        gates.extend(["INV"] * inv_gates)
    if or_gates:
        gates.extend(["OR2"] * or_gates)
    return {"gates": gates, "transistors": transistors, "depth": depth}


def _output_cmos(
    output_mask: tuple[int, int, int, int],
    n_active_hidden: int,
) -> CmosCost:
    """Оценить CMOS-стоимость выходного комбайнера."""
    role, _ = classify_mask(output_mask)

    if output_mask == (1, 0, 0, 1):  # XNOR
        gates = ["NOR2", "AND2", "OR2"]
        return CmosCost(
            gates=gates,
            transistors=sum(_GATE_COST[g][0] for g in gates),
            depth=2,
            notes=[
                "XNOR extracted classic: NOR2 + AND2 + OR2 = 16T, depth 2.",
                "Alternative NAND-only: 4 × NAND2 = 16T.",
                "Optimized XNOR macro/pass-transistor may be cheaper, but it is not counted as the extracted gate network.",
            ],
        )

    if role in ("OR", "AND", "NAND", "NOR", "NOT X0", "NOT X1", "PASS X0", "PASS X1"):
        # Для 2-входовых функций — один вентиль.
        gate_map = {
            "OR": "OR2",
            "AND": "AND2",
            "NAND": "NAND2",
            "NOR": "NOR2",
            "NOT X0": "INV",
            "NOT X1": "INV",
            "PASS X0": None,
            "PASS X1": None,
        }
        g = gate_map[role]
        if g is None:
            return CmosCost([], 0, 0, ["Output is a direct wire (PASS)."])
        cost, depth = _GATE_COST[g]
        return CmosCost([g], cost, depth, [])

    if role in ("XOR", "XNOR", "CUSTOM", "X0 AND NOT X1", "X1 AND NOT X0",
                "X0 → X1", "X1 → X0"):
        terms = _dnf_terms(output_mask)
        cost = _dnf_cost(terms)
        notes = [f"Output role '{role}' implemented as DNF over inputs."]
        return CmosCost(cost["gates"], cost["transistors"], cost["depth"], notes)

    if role in ("ZERO", "ONE"):
        return CmosCost([], 0, 0, ["Output is a constant tied to rail."])

    # fallback — ДНФ
    terms = _dnf_terms(output_mask)
    cost = _dnf_cost(terms)
    return CmosCost(cost["gates"], cost["transistors"], cost["depth"], [])


def estimate_cmos(analysis: ChipAnalysis) -> CmosCost:
    """Полная CMOS-оценка: скрытые вентили + выходной комбайнер."""
    all_gates: list[str] = []
    total_transistors = 0
    max_depth = 0
    notes: list[str] = []
    active_count = 0

    for g in analysis.hidden:
        if g.status == "active":
            active_count += 1
            gates, trans, depth, gate_notes = _hidden_gate_cmos(g)
            all_gates.extend(gates)
            total_transistors += trans
            max_depth = max(max_depth, depth)
            notes.extend(gate_notes)

    output_cost = _output_cmos(analysis.output_mask, active_count)
    all_gates.extend(output_cost.gates)
    total_transistors += output_cost.transistors
    max_depth = max(max_depth, output_cost.depth)
    notes.extend(output_cost.notes)

    # Добавить предупреждения по статусам.
    ignored = [g.index for g in analysis.hidden if g.status == "ignored_by_output"]
    if ignored:
        notes.append(f"Ignored by output: neurons h{', h'.join(str(i) for i in ignored)}.")
    unstable = [g.index for g in analysis.hidden if g.status == "unstable"]
    if unstable:
        notes.append(
            f"Unstable gates (small margin): neurons h{', h'.join(str(i) for i in unstable)}."
        )
    duplicates = [g.index for g in analysis.hidden if g.status == "duplicate"]
    if duplicates:
        notes.append(
            f"Duplicate masks: neurons h{', h'.join(str(i) for i in duplicates)}."
        )

    return CmosCost(
        gates=all_gates,
        transistors=total_transistors,
        depth=max_depth + 1 if all_gates else 0,
        notes=notes,
    )


# -----------------------------------------------------------------------------
# Полный анализ
# -----------------------------------------------------------------------------

def analyze_chip(model_key: str, body: str) -> ChipAnalysis | None:
    """Построить `ChipAnalysis` для поддерживаемой модели."""
    if model_key not in ("mlp", "torch"):
        return None

    parsed = extract_2layer_weights(model_key, body)
    if parsed is None:
        return None

    family, binarize = activation_family(model_key)
    z_values, hidden_masks = hidden_truth_table(parsed.w0, parsed.b0, binarize)

    hidden_gates: list[HiddenGate] = []
    for k in range(parsed.hidden_n):
        role, _ = classify_mask(hidden_masks[k])
        hidden_gates.append(
            HiddenGate(
                index=k,
                role=role,
                mask=hidden_masks[k],
                z_values=z_values[k],
                margin=compute_margin(z_values[k]),
                output_weight=parsed.w1[k],
                status="",  # пересчитается ниже
            )
        )

    hidden_gates = _hidden_statuses(hidden_gates)
    output_mask, output_nets = compute_output_truth_table(
        parsed.w1, parsed.b2, hidden_masks
    )
    output_role, _ = classify_mask(output_mask)
    expression = synthesize_expression(output_mask)

    warnings: list[str] = []
    if any(g.status == "unstable" for g in hidden_gates):
        warnings.append("One or more hidden gates have a small margin and are unstable.")

    analysis = ChipAnalysis(
        model_key=model_key,
        hidden=hidden_gates,
        output_mask=output_mask,
        output_role=output_role,
        expression=expression,
        cmos=CmosCost([], 0, 0, []),  # placeholder
        warnings=warnings,
    )
    analysis = ChipAnalysis(
        model_key=analysis.model_key,
        hidden=analysis.hidden,
        output_mask=analysis.output_mask,
        output_role=analysis.output_role,
        expression=analysis.expression,
        cmos=estimate_cmos(analysis),
        warnings=analysis.warnings,
    )
    return analysis


# -----------------------------------------------------------------------------
# Рендер отчёта
# -----------------------------------------------------------------------------

def _truth_table_rows(
    analysis: ChipAnalysis,
    parsed: Parsed2Layer,
) -> list[str]:
    """Таблица truth table выходного комбайнера."""
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    output_nets = compute_output_truth_table(parsed.w1, parsed.b2,
                                              [g.mask for g in analysis.hidden])[1]
    lines = [
        "| x₀ | x₁ | " + " | ".join(f"h{g.index}" for g in analysis.hidden) + " | net | out |",
        "| :---: | :---: | " + " | ".join([":---:"] * len(analysis.hidden)) + " | :---: | :---: |",
    ]
    for idx, (xi, xj) in enumerate(cases):
        hidden_bits = [str(mask_to_truth(g.mask)[(xi, xj)]) for g in analysis.hidden]
        lines.append(
            f"| {xi} | {xj} | " + " | ".join(hidden_bits)
            + f" | {output_nets[idx]:+.3f} | {analysis.output_mask[idx]} |"
        )
    return lines


def _chip_scheme(analysis: ChipAnalysis) -> str:
    """Текстовая схема чипа."""
    active = [g for g in analysis.hidden if g.status == "active"]
    if not active:
        return "(no active hidden gates; output is driven by bias/constants)"
    gate_line = "  ".join(f"[{g.role}]" for g in active)
    return (
        "        x₀        x₁\n"
        "         │         │\n"
        "    ┌────┴─────────┴────┐\n"
        f"    │  {gate_line}  │\n"
        "    └────────┬────────┘\n"
        "             │\n"
        f"          [{analysis.output_role}]───► output\n"
    )


def render_chip_report(analysis: ChipAnalysis, parsed: Parsed2Layer) -> str:
    """Превратить `ChipAnalysis` в markdown-отчёт."""
    family, _ = activation_family(analysis.model_key)

    lines: list[str] = []
    lines.append("# CHIP ANALYSIS")
    lines.append("")
    lines.append(
        "> Поведение обученной сети дискретизируется на булевых входах 0/1, "
        "после чего из него извлекается логическая схема и оценивается её "
        "эквивалентная CMOS-стоимость в стандартной вентильной библиотеке."
    )
    lines.append("")
    lines.append(f"**Архитектура:** 2 входа → {len(analysis.hidden)} скрытых → 1 выход")
    lines.append(f"**Activation family:** {family}")
    lines.append(f"**Threshold policy:** z ≥ 0 (sigmoid/tanh); z > EPS (ReLU, EPS=1e-6)")
    lines.append("")

    # Шаг 1: hidden gates
    lines.append("## Шаг 1. Hidden gates")
    lines.append("")
    header = "| Нейрон | Роль | mask | z(00) | z(01) | z(10) | z(11) | margin | w_out | Статус |"
    sep = "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    lines.append(header)
    lines.append(sep)
    for g in analysis.hidden:
        zstr = " | ".join(f"{z:+.3f}" for z in g.z_values)
        lines.append(
            f"| **h{g.index}** | **{g.role}** | {list(g.mask)} | {zstr} | "
            f"{g.margin:.3f} | {g.output_weight:+.3f} | {g.status} |"
        )
    lines.append("")

    # Шаг 2: output combiner truth table
    lines.append("## Шаг 2. Output combiner truth table")
    lines.append("")
    lines.extend(_truth_table_rows(analysis, parsed))
    lines.append("")
    lines.append(f"**Output role:** {analysis.output_role}")
    lines.append(f"**Output mask:** {list(analysis.output_mask)}")
    lines.append("")

    # Шаг 3: full chip scheme
    lines.append("## Шаг 3. Full chip scheme")
    lines.append("")
    lines.append("```")
    lines.append(_chip_scheme(analysis))
    lines.append("```")
    lines.append("")

    # Шаг 4: CMOS estimate
    lines.append("## Шаг 4. CMOS estimate")
    lines.append("")
    lines.append(f"**Extracted expression:** `output = {analysis.expression}`")
    lines.append("")
    lines.append(f"**Gates:** {' + '.join(analysis.cmos.gates) if analysis.cmos.gates else '(none)'}")
    lines.append(f"**Transistors:** {analysis.cmos.transistors}T")
    lines.append(f"**Depth:** {analysis.cmos.depth}")
    if analysis.cmos.notes:
        lines.append("")
        for note in analysis.cmos.notes:
            lines.append(f"- *{note}*")
    lines.append("")

    # Шаг 5: verdict
    lines.append("## Шаг 5. Verdict")
    lines.append("")
    if analysis.warnings:
        lines.append("[yellow]Warnings:[/yellow]")
        for w in analysis.warnings:
            lines.append(f"- {w}")
        lines.append("")
    if analysis.output_role == "CUSTOM":
        lines.append(
            "[yellow]Выходная функция не совпала с известной ролью; "
            "построена ДНФ по минтермам.[/yellow]"
        )
    else:
        lines.append(
            f"[green]Сеть дискретизирована как чип: выход `{analysis.output_role}` "
            f"через `{analysis.expression}`.[/green]"
        )
    lines.append("")
    lines.append("(Esc — назад, 1 — chip, 2 — forensic, 3 — prune)")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Плагин
# -----------------------------------------------------------------------------

class ChipEngine:
    info = ReportEngineInfo(
        key="chip",
        hotkey="1",
        title="chip",
        summary="chip / CMOS analysis: извлечение булевой схемы и оценка CMOS",
    )

    @staticmethod
    def render(model_key: str, snapshot_body: str) -> str | None:
        analysis = analyze_chip(model_key, snapshot_body)
        if analysis is None:
            return None
        parsed = extract_2layer_weights(model_key, snapshot_body)
        if parsed is None:
            return None
        return render_chip_report(analysis, parsed)
