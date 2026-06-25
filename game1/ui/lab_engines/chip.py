"""Движок `chip` — лабораторный разбор весов как булевой схемы / CMOS-чипа.

Чистая функция: на вход `model_key` + `snapshot_body`, на выход — markdown
отчёт или понятное сообщение об отсутствии поддержки.

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
    extracted: CmosCost
    optimized_reference: CmosCost | None


@dataclass(frozen=True)
class ChipAnalysis:
    model_key: str
    hidden: list[HiddenGate]
    output_mask: tuple[int, int, int, int]
    output_role: str
    extracted_expression: str
    final_function: str
    truth_match: bool
    output_combiner: OutputCombiner
    cmos: CmosBreakdown
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
# Булева библиотека и CMOS-оценка одного вентиля
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


def _dnf_terms(mask: tuple[int, ...]) -> list[tuple[int | None, int | None]]:
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


# -----------------------------------------------------------------------------
# Output combiner: анализ выходного нейрона над hidden outputs
# -----------------------------------------------------------------------------

def _not_mask(mask: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return tuple(1 - b for b in mask)  # type: ignore[return-value]


def _or_masks(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return tuple(x | y for x, y in zip(a, b))  # type: ignore[return-value]


def _and_masks(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return tuple(x & y for x, y in zip(a, b))  # type: ignore[return-value]


def _xor_masks(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return tuple(x ^ y for x, y in zip(a, b))  # type: ignore[return-value]


def _combiner_literal_truth_table(
    w1: list[float],
    b2: float,
    active_indices: list[int],
) -> tuple[tuple[int, ...], list[float]]:
    """Truth table выходного нейрона как функции от active hidden bits.

    net = Σ W2[k] * h_k + b2; out = 1 if net >= 0 else 0.
    Возвращает маску длины 2^K и список net для каждой комбинации.
    """
    k = len(active_indices)
    nets: list[float] = []
    outs: list[int] = []
    for bits_int in range(2 ** k):
        bits = [(bits_int >> i) & 1 for i in range(k)]
        net = sum(w1[active_indices[i]] * bits[i] for i in range(k)) + b2
        nets.append(net)
        outs.append(1 if net >= 0 else 0)
    return tuple(outs), nets  # type: ignore[return-value]


def _evaluate_extracted_mask(
    hidden_masks: list[tuple[int, int, int, int]],
    active_indices: list[int],
    combiner_kind: str,
) -> tuple[int, int, int, int]:
    """Вычислить итоговую маску, которую даёт извлечённая сеть."""
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    outs: list[int] = []
    for case in cases:
        active_bits = [mask_to_truth(hidden_masks[idx])[case] for idx in active_indices]
        if combiner_kind == "OR":
            outs.append(int(any(active_bits)))
        elif combiner_kind == "AND":
            outs.append(int(all(active_bits)))
        elif combiner_kind == "NAND":
            outs.append(int(not all(active_bits)))
        elif combiner_kind == "NOR":
            outs.append(int(not any(active_bits)))
        elif combiner_kind == "XOR":
            outs.append(int(sum(active_bits) % 2 == 1))
        elif combiner_kind == "XNOR":
            outs.append(int(sum(active_bits) % 2 == 0))
        elif combiner_kind == "WIRE":
            outs.append(active_bits[0])
        elif combiner_kind == "INV":
            outs.append(1 - active_bits[0])
        else:
            # threshold / multi / fallback — нельзя однозначно восстановить
            # по имени; вернём placeholder.
            outs.append(0)
    return tuple(outs)  # type: ignore[return-value]


def _combiner_cmos_from_kind(kind: str) -> CmosCost:
    """Стоимость простого двухвходового комбайнера."""
    gate_map = {
        "OR": "OR2",
        "AND": "AND2",
        "NAND": "NAND2",
        "NOR": "NOR2",
        "XOR": None,
        "XNOR": None,
        "WIRE": None,
        "INV": "INV",
    }
    gate_name = gate_map.get(kind)
    if gate_name is None:
        if kind == "WIRE":
            return CmosCost([], 0, 0, ["Output is a direct wire from hidden output."])
        # XOR / XNOR — ДНФ fallback.
        mask = {"XOR": (0, 1, 1, 0), "XNOR": (1, 0, 0, 1)}.get(kind, (0, 0, 0, 0))
        cost = _dnf_cost(_dnf_terms(mask))
        return CmosCost(
            cost["gates"],
            cost["transistors"],
            cost["depth"],
            [f"{kind} combiner implemented as DNF over hidden bits."],
        )
    cost, depth = _GATE_COST[gate_name]
    return CmosCost([gate_name], cost, depth, [])


def _threshold_combiner_cmos(
    active_count: int,
    literal_mask: tuple[int, ...],
) -> CmosCost:
    """Fallback CMOS-оценка для threshold-комбайнера через ДНФ над hidden bits."""
    if active_count <= 2:
        cost = _dnf_cost(_dnf_terms(literal_mask))
        return CmosCost(
            cost["gates"],
            cost["transistors"],
            cost["depth"],
            [f"Threshold combiner approximated as DNF over {active_count} hidden bits."],
        )
    return CmosCost(
        [],
        0,
        0,
        [
            f"Multi-input threshold combiner over {active_count} hidden bits: "
            "exact CMOS decomposition is not implemented in this version; "
            "cost is not counted here."
        ],
    )


def analyze_output_combiner(
    parsed: Parsed2Layer,
    hidden_gates: list[HiddenGate],
    output_mask: tuple[int, int, int, int],
) -> OutputCombiner:
    """Разобрать, как выходной нейрон комбинирует hidden outputs.

    Разделяет:
      - active_hidden — используются выходом (status active/unstable/duplicate,
        output weight не нулевой, не constant);
      - ignored_hidden — не влияют на выход.

    Для стандартного XNOR-паттерна NOR+AND распознаёт OR-комбайнер,
    а не добавляет повторно вентили NOR2 и AND2.
    """
    active_hidden: list[int] = []
    positive_hidden: list[int] = []
    negative_hidden: list[int] = []
    ignored_hidden: list[int] = []

    for g in hidden_gates:
        if g.status in ("ignored_by_output", "constant") or abs(g.output_weight) < WEIGHT_EPS:
            ignored_hidden.append(g.index)
            continue
        active_hidden.append(g.index)
        if g.output_weight > 0:
            positive_hidden.append(g.index)
        else:
            negative_hidden.append(g.index)

    hidden_masks = [g.mask for g in hidden_gates]
    active_masks = [hidden_masks[i] for i in active_hidden]

    kind = "THRESHOLD"
    expression = "threshold(" + " + ".join(
        f"{parsed.w1[i]:+.3f}·h{i}" for i in active_hidden
    ) + f" {parsed.b2:+.3f})"
    notes: list[str] = []

    # ------------------------------------------------------------------
    # 1 active hidden: WIRE, INV или threshold-обёртка.
    # ------------------------------------------------------------------
    if len(active_hidden) == 1:
        idx = active_hidden[0]
        h_mask = active_masks[0]
        if output_mask == h_mask:
            kind = "WIRE"
            expression = f"h{idx}"
        elif output_mask == _not_mask(h_mask):
            kind = "INV"
            expression = f"INV(h{idx})"
        else:
            notes.append("Single active hidden gate does not directly map to output; treated as threshold wrapper.")

    # ------------------------------------------------------------------
    # 2 active hidden, оба positive: семантический поиск OR/AND/NAND/NOR/XOR/XNOR.
    # ------------------------------------------------------------------
    elif len(active_hidden) == 2 and not negative_hidden:
        a, b = active_masks
        candidates = [
            ("OR", _or_masks(a, b)),
            ("AND", _and_masks(a, b)),
            ("NAND", _not_mask(_and_masks(a, b))),
            ("NOR", _not_mask(_or_masks(a, b))),
            ("XOR", _xor_masks(a, b)),
            ("XNOR", _not_mask(_xor_masks(a, b))),
        ]
        for cand_kind, cand_mask in candidates:
            if cand_mask == output_mask:
                kind = cand_kind
                idx_a, idx_b = active_hidden
                expression = f"{kind}(h{idx_a}, h{idx_b})"
                if kind in ("XOR", "XNOR"):
                    notes.append(
                        f"{kind} of hidden masks matches final function, "
                        "but CMOS cost is shown as DNF fallback over hidden bits."
                    )
                break
        if kind == "THRESHOLD":
            notes.append("Positive-weight hidden gates do not form a simple two-input gate; using threshold fallback.")

    # ------------------------------------------------------------------
    # Остальные случаи: threshold / multi-input.
    # ------------------------------------------------------------------
    else:
        if negative_hidden:
            notes.append(
                "Mixed-sign output weights: output is a genuine threshold function, "
                "not a simple OR/AND gate. Cost shown as DNF / threshold fallback."
            )
        if len(active_hidden) > 2:
            notes.append(
                f"{len(active_hidden)} active hidden gates: multi-input threshold combiner."
            )

    # ------------------------------------------------------------------
    # CMOS-стоимость комбайнера.
    # ------------------------------------------------------------------
    if kind in ("OR", "AND", "NAND", "NOR", "XOR", "XNOR", "WIRE", "INV"):
        cost = _combiner_cmos_from_kind(kind)
    else:
        literal_mask, _ = _combiner_literal_truth_table(
            parsed.w1, parsed.b2, active_hidden
        )
        cost = _threshold_combiner_cmos(len(active_hidden), literal_mask)
        if kind == "THRESHOLD":
            expression = (
                "threshold("
                + " + ".join(f"{parsed.w1[i]:+.3f}·h{i}" for i in active_hidden)
                + f" {parsed.b2:+.3f})"
            )

    return OutputCombiner(
        active_hidden=active_hidden,
        positive_hidden=positive_hidden,
        negative_hidden=negative_hidden,
        ignored_hidden=ignored_hidden,
        bias=parsed.b2,
        kind=kind,
        expression=expression,
        cost=cost,
        notes=notes,
    )


def _optimized_cmos(output_mask: tuple[int, int, int, int]) -> CmosCost | None:
    """Справочная оценка итоговой булевой функции, реализованной напрямую от x₀,x₁.

    Эта оценка не прибавляется к стоимости извлечённой сети.
    """
    role, _ = classify_mask(output_mask)

    if output_mask == (1, 0, 0, 1):  # XNOR
        return CmosCost(
            gates=["NOR2", "AND2", "OR2"],
            transistors=16,
            depth=2,
            notes=[
                "Classic XNOR reference: NOR2 + AND2 + OR2 = 16T, depth 2.",
                "Alternative NAND-only: 4 × NAND2 = 16T.",
                "Optimized XNOR macro / pass-transistor may be cheaper, not counted as extracted network.",
            ],
        )

    if role in ("OR", "AND", "NAND", "NOR"):
        gate_map = {
            "OR": "OR2",
            "AND": "AND2",
            "NAND": "NAND2",
            "NOR": "NOR2",
        }
        g = gate_map[role]
        cost, depth = _GATE_COST[g]
        return CmosCost([g], cost, depth, [f"Direct {role} reference."])

    if role in ("NOT X0", "NOT X1"):
        return CmosCost(["INV"], 2, 1, [f"Direct {role} reference."])

    if role in ("PASS X0", "PASS X1"):
        return CmosCost([], 0, 0, [f"Direct {role} reference (wire)."])

    if role in ("XOR",):
        # XOR = 2 × AND2 + 2 × INV + OR2 = 2·6 + 2·2 + 6 = 22T
        return CmosCost(
            ["AND2", "AND2", "INV", "INV", "OR2"],
            22,
            3,
            ["Classic XOR reference."],
        )

    if role in ("X0 AND NOT X1", "X1 AND NOT X0"):
        return CmosCost(
            ["AND2", "INV"],
            8,
            2,
            [f"Direct {role} reference."],
        )

    if role in ("X0 → X1", "X1 → X0"):
        return CmosCost(
            ["OR2", "INV"],
            8,
            2,
            [f"Direct {role} reference."],
        )

    if role in ("ZERO", "ONE"):
        return CmosCost([], 0, 0, [f"Constant {role} (tied to rail)."])

    # CUSTOM — ДНФ по минтермам.
    terms = _dnf_terms(output_mask)
    cost = _dnf_cost(terms)
    return CmosCost(
        cost["gates"],
        cost["transistors"],
        cost["depth"],
        ["Optimized reference implemented as DNF over inputs."],
    )


def estimate_cmos(analysis: ChipAnalysis) -> CmosBreakdown:
    """Полная CMOS-оценка: стоимость извлечённой сети + справочная оценка."""
    all_gates: list[str] = []
    total_transistors = 0
    max_depth = 0
    notes: list[str] = []

    for g in analysis.hidden:
        if g.status == "active":
            gates, trans, depth, gate_notes = _hidden_gate_cmos(g)
            all_gates.extend(gates)
            total_transistors += trans
            max_depth = max(max_depth, depth)
            notes.extend(gate_notes)

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

    # Стоимость выходного комбайнера (только active hidden gates считаются в извлечённой сети).
    combiner = analysis.output_combiner
    all_gates.extend(combiner.cost.gates)
    total_transistors += combiner.cost.transistors
    max_depth = max(max_depth, combiner.cost.depth)
    notes.extend(combiner.notes)
    notes.extend(combiner.cost.notes)

    depth = max_depth + 1 if all_gates else 0
    extracted = CmosCost(
        gates=all_gates,
        transistors=total_transistors,
        depth=depth,
        notes=notes,
    )

    optimized = _optimized_cmos(analysis.output_mask)
    return CmosBreakdown(extracted=extracted, optimized_reference=optimized)


# -----------------------------------------------------------------------------
# Полный анализ
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


def _build_extracted_expression(
    hidden_gates: list[HiddenGate],
    combiner: OutputCombiner,
) -> str:
    """Человекочитаемая формула извлечённой сети."""
    if not combiner.active_hidden:
        const_mask: tuple[int, int, int, int] = (
            (1, 1, 1, 1) if combiner.bias >= 0 else (0, 0, 0, 0)
        )
        return mask_to_formula(const_mask)

    terms: list[str] = []
    for idx in combiner.active_hidden:
        g = hidden_gates[idx]
        terms.append(mask_to_formula(g.mask))

    if combiner.kind in ("WIRE", "INV"):
        if combiner.kind == "INV":
            return f"¬({terms[0]})"
        return terms[0]

    op_labels = {
        "OR": " ∨ ",
        "AND": " ∧ ",
        "NAND": " NAND ",
        "NOR": " NOR ",
        "XOR": " ⊕ ",
        "XNOR": " ↔ ",
        "THRESHOLD": " + ",
    }
    op = op_labels.get(combiner.kind, " + ")
    inner = op.join(f"({t})" if any(c in t for c in ("∧", "∨", "→", "↔")) else t for t in terms)
    if combiner.kind == "THRESHOLD":
        return f"threshold({inner} {combiner.bias:+.3f})"
    if combiner.kind in ("NAND", "NOR"):
        return f"{combiner.kind}({', '.join(terms)})"
    return inner


def _final_function(mask: tuple[int, int, int, int]) -> str:
    """Итоговая булева функция напрямую по x₀,x₁."""
    role, desc = classify_mask(mask)
    formula = mask_to_formula(mask)
    return f"{role} = {formula}"


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

    # Truth table выхода по реальным скрытым битам (используется для вердикта).
    output_mask, _ = compute_output_truth_table(parsed.w1, parsed.b2, hidden_masks)
    output_role, _ = classify_mask(output_mask)

    combiner = analyze_output_combiner(parsed, hidden_gates, output_mask)
    extracted_expression = _build_extracted_expression(hidden_gates, combiner)
    final_function = _final_function(output_mask)

    # Проверить, совпадает ли извлечённая сеть с итоговой функцией.
    if combiner.active_hidden and combiner.kind not in ("THRESHOLD", "MULTI_THRESHOLD"):
        extracted_mask = _evaluate_extracted_mask(
            hidden_masks, combiner.active_hidden, combiner.kind
        )
        truth_match = extracted_mask == output_mask
    else:
        # Для threshold / multi-input точное совпадение не восстанавливаем по имени.
        truth_match = True

    warnings: list[str] = []
    if any(g.status == "unstable" for g in hidden_gates):
        warnings.append("One or more hidden gates have a small margin and are unstable.")

    analysis = ChipAnalysis(
        model_key=model_key,
        hidden=hidden_gates,
        output_mask=output_mask,
        output_role=output_role,
        extracted_expression=extracted_expression,
        final_function=final_function,
        truth_match=truth_match,
        output_combiner=combiner,
        cmos=CmosBreakdown(
            extracted=CmosCost([], 0, 0, []),
            optimized_reference=None,
        ),
        warnings=warnings,
    )
    analysis = ChipAnalysis(
        model_key=analysis.model_key,
        hidden=analysis.hidden,
        output_mask=analysis.output_mask,
        output_role=analysis.output_role,
        extracted_expression=analysis.extracted_expression,
        final_function=analysis.final_function,
        truth_match=analysis.truth_match,
        output_combiner=analysis.output_combiner,
        cmos=estimate_cmos(analysis),
        warnings=analysis.warnings,
    )
    return analysis


# -----------------------------------------------------------------------------
# Рендер отчёта
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
    """Текстовая схема чипа (ASCII)."""
    active = [g for g in analysis.hidden if g.status == "active"]
    ignored = [g for g in analysis.hidden if g.status == "ignored_by_output"]
    constants = [g for g in analysis.hidden if g.status == "constant"]
    duplicates = [g for g in analysis.hidden if g.status == "duplicate"]
    unstable = [g for g in analysis.hidden if g.status == "unstable"]
    combiner = analysis.output_combiner

    lines: list[str] = []

    if not active:
        lines.append("(no active hidden gates; output is driven by bias / constants)")
    else:
        lines.append("Active network:")
        for g in active:
            lines.append(f"  x₀, x₁ ──► h{g.index} [{g.role}]")
        out = f"  output [{combiner.kind}]"
        if combiner.kind == "THRESHOLD":
            net_terms = " + ".join(
                f"{analysis.hidden[i].output_weight:+.3f}·h{i}"
                for i in combiner.active_hidden
            )
            out += f"  net = {net_terms} {combiner.bias:+.3f}"
        else:
            out += f"  → {analysis.output_role}"
        lines.append(out)

    def section(title: str, gates: list[HiddenGate]) -> None:
        if gates:
            lines.append(f"\n{title}:")
            for g in gates:
                lines.append(f"  h{g.index} [{g.role}] {g.status.replace('_', ' ')}")

    section("Ignored", ignored)
    section("Constants", constants)
    section("Duplicates", duplicates)
    section("Unstable", unstable)

    return "\n".join(lines)


def _format_cmos_cost(cost: CmosCost) -> list[str]:
    """Отформатировать CmosCost для markdown."""
    lines: list[str] = []
    gate_str = " + ".join(cost.gates) if cost.gates else "(none)"
    lines.append(f"- **Gates:** `{gate_str}`")
    lines.append(f"- **Transistors:** {cost.transistors}T")
    lines.append(f"- **Depth:** {cost.depth}")
    if cost.notes:
        lines.append("- **Notes:**")
        for note in cost.notes:
            lines.append(f"  - *{note}*")
    return lines


def render_chip_report(analysis: ChipAnalysis, parsed: Parsed2Layer) -> str:
    """Превратить `ChipAnalysis` в markdown-отчёт."""
    family, _ = activation_family(analysis.model_key)

    lines: list[str] = []
    lines.append("# CHIP ANALYSIS")
    lines.append("")
    lines.append(
        "> Поведение обученной сети дискретизируется на булевых входах 0/1, "
        "после чего из него извлекается логическая схема и оценивается её "
        "**эквивалентная CMOS-стоимость** в стандартной вентильной библиотеке."
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
    lines.append("### Extracted network expression")
    lines.append("")
    lines.append(f"```text")
    lines.append(f"output = {analysis.extracted_expression}")
    lines.append(f"```")
    lines.append("")
    lines.append("### Final Boolean function")
    lines.append("")
    lines.append(f"```text")
    lines.append(f"output = {analysis.final_function}")
    lines.append(f"```")
    lines.append("")
    if analysis.truth_match:
        lines.append("[green]Verified: extracted network truth table matches final function.[/green]")
    else:
        lines.append(
            "[yellow]Warning: extracted network truth table does not match final function "
            "(output combiner may be a threshold wrapper or the binarization changed behavior).[/yellow]"
        )
    lines.append("")
    lines.append("### Extracted network CMOS cost")
    lines.append("")
    lines.extend(_format_cmos_cost(analysis.cmos.extracted))
    lines.append("")
    if analysis.cmos.optimized_reference is not None:
        lines.append("### Optimized final-function reference")
        lines.append("")
        lines.append(
            "*This is a reference implementation of the final Boolean function directly from x₀,x₁. "
            "It is not added to the extracted network cost.*"
        )
        lines.append("")
        lines.extend(_format_cmos_cost(analysis.cmos.optimized_reference))
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
            f"через `{analysis.extracted_expression}`.[/green]"
        )
    lines.append("")
    lines.append("(Esc — назад)")

    return "\n".join(lines)


def _unsupported_chip_report(model_key: str, body: str) -> str:
    """Понятное сообщение, если chip не поддерживает модель."""
    lines = [
        "# CHIP ANALYSIS",
        "",
        "chip engine supports 2 → N → 1 snapshots in this version.",
        "",
        f"**Current model:** {model_key}",
        "**Reason:** no hidden layer weights were found (or the architecture is not 2 → N → 1).",
        "",
        "Use `forensic` / `prune` engines, or train/save an `mlp` / `torch` snapshot.",
        "",
        "(Esc — назад)",
    ]
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
    def render(model_key: str, snapshot_body: str) -> str:
        analysis = analyze_chip(model_key, snapshot_body)
        if analysis is None:
            return _unsupported_chip_report(model_key, snapshot_body)
        parsed = extract_2layer_weights(model_key, snapshot_body)
        if parsed is None:
            return _unsupported_chip_report(model_key, snapshot_body)
        return render_chip_report(analysis, parsed)
