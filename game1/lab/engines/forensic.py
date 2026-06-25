"""Движок `forensic` — следовательский разбор весов.

Сохраняет поведение оригинального forensic_report: активации считаются через
post-activation threshold (`σ(z) > 0.5`), роли сопоставляются с известными
мас-ками, подбирается логическое уравнение.
"""
from __future__ import annotations

import math
import re
from itertools import combinations

from .base import ReportEngineInfo
from .common import (
    Parsed2Layer,
    activation_family as _new_activation_family,
    classify_mask,
    extract_2layer_weights,
    join_formula_terms,
    mask_to_formula,
    mask_to_truth,
    parse_weights,
    to_code_formula,
)


# -----------------------------------------------------------------------------
# Локальные утилиты forensic (поведение оригинала)
# -----------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    if x < -50:
        return 0.0
    if x > 50:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _hidden_activation(model_key: str):
    """Оригинальная активация: forensic бинаризует через σ(z) > 0.5."""
    family, _ = _new_activation_family(model_key)
    if model_key == "torch":
        act, act_name = math.tanh, "Tanh"
    elif model_key == "mlp":
        act, act_name = lambda v: max(0.0, v), "ReLU"
    else:
        act, act_name = _sigmoid, "Sigmoid"

    def bit_fn(z: float) -> int:
        return 1 if act(z) > 0.5 else 0

    return bit_fn, act, act_name


_ROLE_MEANINGS: dict[tuple[int, ...], tuple[str, str]] = {
    (0, 0, 0, 0): ("ZERO", "Постоянный ноль / мёртв"),
    (1, 1, 1, 1): ("ONE", "Постоянная единица / застрял"),
    (0, 1, 1, 1): ("OR", "Активен, если хоть один вход = 1"),
    (0, 0, 0, 1): ("AND", "Активен, только если оба входа = 1"),
    (1, 1, 1, 0): ("NAND", "Активен всегда, кроме случая оба = 1"),
    (1, 0, 0, 0): ("NOR", "Активен, только если оба входа = 0"),
    (0, 1, 1, 0): ("XOR", "Активен, когда входы разные"),
    (1, 0, 0, 1): ("XNOR", "Активен, когда входы совпадают"),
    (0, 0, 1, 1): ("PASS X1", "Повторяет за Свидетелем 1"),
    (0, 1, 0, 1): ("PASS X2", "Повторяет за Свидетелем 2"),
    (1, 1, 0, 0): ("NOT X1", "Инверсия Свидетеля 1"),
    (1, 0, 1, 0): ("NOT X2", "Инверсия Свидетеля 2"),
    (0, 0, 1, 0): ("X1 AND NOT X2", "Только Свидетель 1"),
    (0, 1, 0, 0): ("X2 AND NOT X1", "Только Свидетель 2"),
    (1, 1, 0, 1): ("X1 → X2", "Импликация X1 ведёт к X2"),
    (1, 0, 1, 1): ("X2 → X1", "Импликация X2 ведёт к X1"),
}

_BIAS_MASK: tuple[int, ...] = (1, 1, 1, 1)


def _evaluate_formula(
    pos_masks: list[tuple[int, ...]],
    neg_masks: list[tuple[int, ...]],
    target: dict[tuple[int, int], int],
) -> tuple[dict[tuple[int, int], int], bool]:
    answer: dict[tuple[int, int], int] = {}
    for case in [(0, 0), (0, 1), (1, 0), (1, 1)]:
        pos_val = any(mask_to_truth(m)[case] for m in pos_masks) if pos_masks else 1
        neg_val = any(mask_to_truth(m)[case] for m in neg_masks) if neg_masks else 0
        answer[case] = 1 if pos_val and not neg_val else 0
    is_correct = all(answer[c] == target[c] for c in answer)
    return answer, is_correct


def _build_logic_equation(
    effective_pos: list[int],
    effective_neg: list[int],
    masks: dict[int, tuple[int, ...]],
    target: dict[tuple[int, int], int],
    bias: float,
    bias_threshold: float = 1.0,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], bool, str]:
    pos_role_masks = [masks[k] for k in effective_pos]
    neg_role_masks = [masks[k] for k in effective_neg]
    pos_masks = list(pos_role_masks)
    neg_masks = list(neg_role_masks)
    bias_note = ""

    if bias > bias_threshold:
        pos_masks.append(_BIAS_MASK)
        bias_note = f"bias {bias:+.3f} держит базовый уровень 1"
    elif bias < -bias_threshold:
        neg_masks.append(_BIAS_MASK)
        bias_note = f"bias {bias:+.3f} держит базовый уровень 0"

    if not pos_masks and not neg_masks:
        return [], [], False, ""

    best: tuple[list[tuple[int, ...]], list[tuple[int, ...]], bool] = ([], [], False)
    best_errors = 5

    pos_indices = list(range(len(pos_masks)))
    neg_indices = list(range(len(neg_masks)))

    for pos_r in range(len(pos_indices) + 1):
        for pos_subset in combinations(pos_indices, pos_r):
            for neg_r in range(len(neg_indices) + 1):
                for neg_subset in combinations(neg_indices, neg_r):
                    if not pos_subset and not neg_subset:
                        continue
                    sub_pos = [pos_masks[i] for i in pos_subset]
                    sub_neg = [neg_masks[j] for j in neg_subset]
                    ans, ok = _evaluate_formula(sub_pos, sub_neg, target)
                    if ok:
                        return sub_pos, sub_neg, True, bias_note
                    errors = sum(1 for c in ans if ans[c] != target[c])
                    if errors < best_errors:
                        best_errors = errors
                        best = (sub_pos, sub_neg, False)

    return best[0], best[1], False, bias_note


# -----------------------------------------------------------------------------
# Сам отчёт
# -----------------------------------------------------------------------------

def forensic_report(model_key: str, body: str) -> str | None:
    parsed = extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b2 = parsed.hidden_n, parsed.w0, parsed.b0, parsed.w1, parsed.b2

    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    bit_fn, act, act_name = _hidden_activation(model_key)

    roles: dict[int, tuple[str, str]] = {}
    masks: dict[int, tuple[int, ...]] = {}
    for k in range(hidden_n):
        outs = []
        for xi, xj in cases:
            z = xi * w0[0][k] + xj * w0[1][k] + b0[k]
            outs.append(bit_fn(z))
        mask = tuple(outs)
        masks[k] = mask
        roles[k] = _ROLE_MEANINGS.get(mask, ("CUSTOM", f"Кастомный триггер {list(mask)}"))

    effective_pos = [
        k for k in range(hidden_n)
        if abs(w1[k]) >= 0.01 and masks[k] not in ((0, 0, 0, 0), (1, 1, 1, 1)) and w1[k] > 0
    ]
    effective_neg = [
        k for k in range(hidden_n)
        if abs(w1[k]) >= 0.01 and masks[k] not in ((0, 0, 0, 0), (1, 1, 1, 1)) and w1[k] < 0
    ]

    target_table = {c: (1 if c[0] == c[1] else 0) for c in cases}
    target_name = "XNOR"
    pos_masks, neg_masks, exact, bias_note = _build_logic_equation(
        effective_pos, effective_neg, masks, target_table, b2
    )

    real_pos = [m for m in pos_masks if m != _BIAS_MASK]
    bias_pos = [m for m in pos_masks if m == _BIAS_MASK]
    real_neg = [m for m in neg_masks if m != _BIAS_MASK]
    bias_neg = [m for m in neg_masks if m == _BIAS_MASK]

    pos_formulas = [mask_to_formula(m) for m in real_pos]
    neg_formulas = [mask_to_formula(m) for m in real_neg]
    pos_names = [_ROLE_MEANINGS.get(m, ("?", "?"))[0] for m in real_pos]
    neg_names = [_ROLE_MEANINGS.get(m, ("?", "?"))[0] for m in real_neg]

    pos_part = join_formula_terms(pos_formulas, "∨")
    neg_part = join_formula_terms(neg_formulas, "∨")

    if pos_part and neg_part:
        equation_scientific = f"({pos_part}) ∧ ¬({neg_part}) = {target_name}"
        equation_code = to_code_formula(f"({pos_part}) && !({neg_part}) == true")
    elif pos_part:
        equation_scientific = f"{pos_part} = {target_name}"
        equation_code = to_code_formula(f"{pos_part} == true")
    elif neg_part:
        equation_scientific = f"¬({neg_part}) = {target_name}"
        equation_code = to_code_formula(f"!({neg_part}) == true")
    else:
        equation_scientific = f"? = {target_name}"
        equation_code = "? == true"

    answer_table, equation_ok = _evaluate_formula(pos_masks, neg_masks, target_table)
    all_ok = equation_ok
    verification_rows = []
    for xi, xj in cases:
        case = (xi, xj)
        pos_vals = [mask_to_truth(m)[case] for m in pos_masks]
        neg_vals = [mask_to_truth(m)[case] for m in neg_masks]
        eq_val = answer_table[case]

        h_vals = [act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) for k in range(hidden_n)]
        net = sum(w1[k] * h_vals[k] for k in range(hidden_n)) + b2
        net_val = 1 if _sigmoid(net) > 0.5 else 0
        target = target_table[case]

        ok = eq_val == target == net_val
        all_ok = all_ok and ok
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"

        terms = [str(int(v)) for v in pos_vals]
        if bias_pos:
            terms.append("1")
        terms_str = " + ".join(terms) if terms else "0"

        verification_rows.append((xi, xj, terms_str, eq_val, net_val, target, mark))

    active_pos: list[int] = []
    active_neg: list[int] = []
    ignored: list[int] = []
    seen: dict[tuple[int, ...], int] = {}
    duplicates: list[tuple[int, int]] = []

    for k in range(hidden_n):
        weight = w1[k]
        mask = masks[k]
        if abs(weight) < 0.01 or mask in ((0, 0, 0, 0), (1, 1, 1, 1)):
            ignored.append(k)
        elif mask in seen:
            duplicates.append((k, seen[mask]))
        else:
            seen[mask] = k
            if weight > 0:
                active_pos.append(k)
            else:
                active_neg.append(k)

    lines: list[str] = []
    lines.append("# ОТЧЁТ: АНАЛИЗ ВЕСОВ МОДЕЛИ (XNOR)")
    lines.append("")
    lines.append(f"**Цель:** Проверка логики сети 2 → {hidden_n} → 1 (задача «Два свидетеля»).")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## ШАГ 1. Фильтрация и роли нейронов")
    lines.append("")

    role_table = [
        "| Нейрон | Роль | Поведение | Вес | Статус |",
        "| :---: | :---: | :--- | :---: | :---: |",
    ]
    for k in range(hidden_n):
        weight = w1[k]
        mask = masks[k]
        term, meaning = roles[k]

        if abs(weight) < 0.01 or mask in ((0, 0, 0, 0), (1, 1, 1, 1)):
            status = "[red]Игнор[/red]"
            role_cell = term
        elif weight > 0:
            status = "[green]**АКТИВЕН (+)**[/green]"
            role_cell = f"**{term}**"
        else:
            status = "[yellow]**АКТИВЕН (-)**[/yellow]"
            role_cell = f"**{term}**"

        role_table.append(
            f"| **h{k}** | {role_cell} | {meaning} | {weight:+.2f} | {status} |"
        )
    lines.extend(role_table)

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## ШАГ 2. Логическая формула и верификация")
    lines.append("")
    lines.append(f"**Уравнение:** {equation_scientific}")
    lines.append("")
    if bias_note:
        lines.append(f"*{bias_note}.*")
        lines.append("")

    verify_table = [
        "| x₀ | x₁ | Слагаемые | Уравнение | Сеть | Target | OK |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for xi, xj, terms_str, eq_val, net_val, target, mark in verification_rows:
        eq_bold = f"**{eq_val}**"
        verify_table.append(
            f"| {xi} | {xj} | {terms_str} | {eq_bold} | {net_val} | {target} | {mark} |"
        )
    lines.extend(verify_table)

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## ШАГ 3. Вердикт")
    lines.append("")

    role_descriptions = []
    for k in active_pos:
        role_descriptions.append(f"**{roles[k][0]}**")
    for k in active_neg:
        role_descriptions.append(f"**{roles[k][0]}** (вычитание)")

    if all_ok:
        lines.append("⚖️ [green]**Уравнение детектора верно.**[/green]")
        lines.append("")
        if role_descriptions:
            lines.append(
                f"Сеть разложила {target_name} на подзадачи: {', '.join(role_descriptions)}. "
                "Выходной нейрон объединяет их через логическое сложение (+). "
                "Результаты уравнения, сети и target полностью совпадают."
            )
        else:
            lines.append("Сеть корректно решает задачу через выходной bias.")
    else:
        lines.append("⚖️ [red]**Уравнение детектора неверно.**[/red]")
        lines.append("")
        lines.append(
            "[red]Роли не складываются в целевую функцию — либо детектор сглючил, "
            "либо сеть не решила задачу.[/red]"
        )

    if ignored:
        lines.append("")
        lines.append(
            f"[yellow]Примечание:[/yellow] нейроны h{', h'.join(str(k) for k in ignored)} "
            "не участвуют в решении (игнорируются)."
        )
    if duplicates:
        lines.append("")
        for k, prev in duplicates:
            same_sign = (w1[k] > 0 and w1[prev] > 0) or (w1[k] < 0 and w1[prev] < 0)
            note = "работают заодно" if same_sign else "[red]гасят друг друга[/red]"
            lines.append(f"[yellow]Дубликат:[/yellow] h{k} повторяет h{prev} — {note}.")

    if not exact:
        lines.append("")
        lines.append(
            "[yellow]Примечание:[/yellow] подобрано ближайшее приближение; возможно, "
            "детектору не хватило чистых OR/XOR-ролей."
        )

    lines.append("")
    lines.append("(Esc — назад, 1 — chip, 2 — forensic, 3 — prune)")

    return "\n".join(lines)


class ForensicEngine:
    info = ReportEngineInfo(
        key="forensic",
        hotkey="2",
        title="forensic",
        summary="forensic: sigmoid-роли и следовательский разбор",
    )

    @staticmethod
    def render(model_key: str, snapshot_body: str) -> str | None:
        return forensic_report(model_key, snapshot_body)
