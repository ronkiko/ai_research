"""Движки отчётов для Лаборатории (LabPane).

Чистые функции: на вход — ключ модели и тело .md-снапшота, на выход — текст
детального разбора или None, если разбор невозможен. Не зависят от curses/UI.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable

def _sigmoid(x: float) -> float:
    """Защищённый от переполнения сигмоид."""
    if x < -50:
        return 0.0
    if x > 50:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _visible_len(text: str) -> int:
    """Длина строки без цветовых тегов [color]...[/color]."""
    out = re.sub(r"\[/?[a-z]+\]", "", text)
    # Эмодзи/широкие символы могут считаться за 1 или 2; для простоты len().
    return len(out)


def _center(text: str, width: int) -> str:
    """Отцентрировать text с учётом видимой длины (цветные теги не влияют).

    Если видимый текст длиннее width — обрезается с сохранением цветового тега.
    """
    vis = _visible_len(text)
    if vis > width:
        plain = re.sub(r"\[/?[a-z]+\]", "", text)
        truncated = plain[: max(0, width - 1)] + "…"
        color_match = re.search(r"\[([a-z]+)\]", text)
        if color_match and f"[/{color_match.group(1)}]" in text:
            color = color_match.group(1)
            text = f"[{color}]{truncated}[/{color}]"
        else:
            text = truncated
        vis = _visible_len(text)
    pad = max(0, width - vis)
    left = pad // 2
    right = pad - left
    return " " * left + text + " " * right


# ---------------------------------------------------------------------------
# Парсер весов из markdown-снапшота
# ---------------------------------------------------------------------------

_TABLE_HEADER_RE = re.compile(r"нейрон\s+(\d+)", re.IGNORECASE)
_SECOND_LAYER_RE = re.compile(r"(второго|second|выходного|output)", re.IGNORECASE)


def parse_weights(body: str) -> dict[str, float]:
    """Извлечь веса из тела снапшота.

    Поддерживает два формата:
      - inline:  `0.weight[0]` = 5.5197
      - табличный:
            нейрон 0  нейрон 1  нейрон 2  нейрон 3
        weight[x0]   -0.257    -3.223    -0.697    +2.800
        weight[x1]   -0.269    -3.226    -0.652    +2.801
        bias         -0.225    +3.220    -0.256    -2.801

    Возвращает словарь вида {"0.weight[0]": 5.5197, "0.bias[0]": -2.5783,
    "2.weight[0]": -5.3611, "2.bias[0]": -4.9205}.
    """
    weights: dict[str, float] = {}

    # --- inline формат ---
    for line in body.splitlines():
        m = re.match(r"\s*`(.+?)`\s*=\s*([-\d.]+)", line)
        if m:
            try:
                weights[m.group(1)] = float(m.group(2))
            except ValueError:
                pass

    # --- табличный формат ---
    stage = 0  # 0 — первый слой, 1 — второй слой
    table_neurons: list[int] | None = None

    for raw in body.splitlines():
        line = raw.rstrip()

        if line.startswith("#"):
            if _SECOND_LAYER_RE.search(line):
                stage = 1
            table_neurons = None
            continue

        header_matches = _TABLE_HEADER_RE.findall(line)
        if header_matches:
            table_neurons = [int(x) for x in header_matches]
            continue

        if table_neurons is None or line.strip() == "":
            continue

        parts = line.split()
        if not parts:
            continue

        token = parts[0]
        values = parts[1:]
        if len(values) < len(table_neurons):
            continue

        if token.startswith("weight"):
            if "[x0]" in token:
                for n, v in zip(table_neurons, values):
                    weights[f"0.weight[{n * 2 + 0}]"] = float(v)
            elif "[x1]" in token:
                for n, v in zip(table_neurons, values):
                    weights[f"0.weight[{n * 2 + 1}]"] = float(v)
            else:
                # второй слой
                for n, v in zip(table_neurons, values):
                    weights[f"2.weight[{n}]"] = float(v)

        elif token.startswith("bias"):
            if stage == 0:
                for n, v in zip(table_neurons, values):
                    weights[f"0.bias[{n}]"] = float(v)
            else:
                # один выходной нейрон — берём первое значение
                weights["2.bias[0]"] = float(values[0])

        else:
            # таблица закончилась
            table_neurons = None

    return weights


# ---------------------------------------------------------------------------
# Утилиты общие для обоих движков
# ---------------------------------------------------------------------------


def _extract_2layer_weights(model_key: str, body: str) -> tuple[int, list[list[float]], list[float], list[float], float] | None:
    """Получить параметры сети 2 → N → 1.

    Возвращает (hidden_n, W1, b1, W2, b2) или None, если параметров нет.
    W1[in_idx][k] — вес от входа in_idx к скрытому нейрону k.
    """
    w = parse_weights(body)

    hidden_n = 0
    while f"0.bias[{hidden_n}]" in w:
        hidden_n += 1
    if hidden_n < 1:
        return None

    # Определяем формат индексации весов первого слоя.
    # Inline формат PyTorch: 0.weight[0..{hidden_n*2-1}], где чётные — x₀, нечётные — x₁.
    # Табличный формат: 0.weight[{k*2+in_idx}] (две строки weight[x0], weight[x1]).
    inline_format = all(f"0.weight[{k}]" in w for k in range(hidden_n * 2))
    table_format = not inline_format and all(
        f"0.weight[{k * 2 + 0}]" in w or f"0.weight[{k * 2 + 1}]" in w
        for k in range(hidden_n)
    )
    if not (table_format or inline_format):
        return None

    w0: list[list[float]] = []
    if inline_format:
        # inline: чётные индексы — от x₀, нечётные — от x₁.
        for in_idx in range(2):
            w0.append([w.get(f"0.weight[{k * 2 + in_idx}]", 0.0) for k in range(hidden_n)])
    else:
        for in_idx in range(2):
            w0.append([w.get(f"0.weight[{k * 2 + in_idx}]", 0.0) for k in range(hidden_n)])

    b0 = [w.get(f"0.bias[{k}]", 0.0) for k in range(hidden_n)]
    w1 = [w.get(f"2.weight[{k}]", 0.0) for k in range(hidden_n)]
    b2 = w.get("2.bias[0]", 0.0)

    return hidden_n, w0, b0, w1, b2


def _hidden_activation(model_key: str) -> tuple[Callable[[float], float], str]:
    """Вернуть активацию скрытого слоя для данной модели."""
    if model_key == "torch":
        return math.tanh, "Tanh"
    if model_key == "mlp":
        return lambda v: max(0.0, v), "ReLU"
    return _sigmoid, "Sigmoid"


# ---------------------------------------------------------------------------
# Движок 1 — классический разбор (Tanh для torch, ReLU для mlp)
# ---------------------------------------------------------------------------


def _compute_roles(
    hidden_n: int,
    w0: list[list[float]],
    b0: list[float],
    model_key: str,
) -> tuple[dict[int, tuple[str, str]], dict[int, tuple[int, ...]]]:
    """Определить логические роли скрытых нейронов как цифровые вентили."""
    act, _ = _hidden_activation(model_key)
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    roles: dict[int, tuple[str, str]] = {}
    masks: dict[int, tuple[int, ...]] = {}
    for k in range(hidden_n):
        outs = []
        for xi, xj in cases:
            z = xi * w0[0][k] + xj * w0[1][k] + b0[k]
            outs.append(1 if act(z) > 0.5 else 0)
        mask = tuple(outs)
        masks[k] = mask
        roles[k] = _ROLE_NAMES.get(mask, ("CUSTOM", f"Кастомный триггер {list(mask)}"))
    return roles, masks


def _chip_diagram(
    pos_roles: list[str],
    neg_roles: list[str],
    target_name: str,
    bias: float,
) -> str:
    """ASCII-схема сборки логических вентилей в чип."""
    lines: list[str] = ["СХЕМА СБОРКИ ВЕНТИЛЕЙ:", ""]

    gates: list[str] = []
    for r in pos_roles:
        gates.append(f"[{r}]")
    for r in neg_roles:
        gates.append(f"[{r}]→[NOT]")

    if not gates:
        lines.append("    (нет активных вентилей)")
        if abs(bias) > 0.5:
            lines.append(f"    bias {bias:+.3f} держит базовый уровень")
        return "\n".join(lines)

    lines.append("        x₀        x₁")
    lines.append("         │         │")
    lines.append("    ┌────┴─────────┴────┐")
    lines.append("    │                   │")
    lines.append("    │  " + "    ".join(gates) + "  │")
    lines.append("    │                   │")
    lines.append("    └────────┬────────┘")
    lines.append("             │")
    lines.append(f"          [ OR ]───► {target_name}")
    if abs(bias) > 0.5:
        lines.append("             ▲")
        lines.append(f"        (bias {bias:+.3f})")

    return "\n".join(lines)


def default_report(model_key: str, body: str) -> str | None:
    """Схемотехнический разбор для torch/mlp: нейроны как логические вентили."""
    if model_key not in ("torch", "mlp"):
        return None

    parsed = _extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b1 = parsed

    act, act_name = _hidden_activation(model_key)
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    roles, masks = _compute_roles(hidden_n, w0, b0, model_key)

    active_pos = [
        k for k in range(hidden_n)
        if w1[k] > 0 and abs(w1[k]) >= 0.01 and masks[k] not in ((0, 0, 0, 0), (1, 1, 1, 1))
    ]
    active_neg = [
        k for k in range(hidden_n)
        if w1[k] < 0 and abs(w1[k]) >= 0.01 and masks[k] not in ((0, 0, 0, 0), (1, 1, 1, 1))
    ]
    ignored = [k for k in range(hidden_n) if k not in active_pos and k not in active_neg]

    pos_roles = [roles[k][0] for k in active_pos]
    neg_roles = [roles[k][0] for k in active_neg]

    target_table = {c: (1 if c[0] == c[1] else 0) for c in cases}
    target_name = "XNOR"

    pos_part = _join_formula_terms([_mask_to_formula(masks[k]) for k in active_pos], "∨")
    neg_part = _join_formula_terms([_mask_to_formula(masks[k]) for k in active_neg], "∨")
    if pos_part and neg_part:
        equation = f"({pos_part}) ∧ ¬({neg_part}) = {target_name}"
    elif pos_part:
        equation = f"{pos_part} = {target_name}"
    elif neg_part:
        equation = f"¬({neg_part}) = {target_name}"
    else:
        equation = f"? = {target_name}"

    def sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    lines: list[str] = []
    lines.append("### ШАГ 1. Вентили скрытого слоя")
    lines.append("")
    lines.append(f"Архитектура: 2 входа → {hidden_n} скрытых ({act_name}) → 1 выход (sigmoid)")
    lines.append("")

    gate_table = [
        "| Нейрон | Вентиль | Статус |",
        "| :---: | :---: | :---: |",
    ]
    for k in range(hidden_n):
        role_name, _ = roles[k]
        weight = w1[k]
        mask = masks[k]
        if abs(weight) < 0.01 or mask in ((0, 0, 0, 0), (1, 1, 1, 1)):
            status = "[red]Игнор[/red]"
        elif weight > 0:
            status = "[green]**АКТИВЕН (+)**[/green]"
        else:
            status = "[yellow]**АКТИВЕН (−)**[/yellow]"
        gate_table.append(f"| **h{k}** | **{role_name}** | {status} |")
    lines.extend(gate_table)
    lines.append("")

    lines.append("### ШАГ 2. Логическая схема и верификация")
    lines.append("")
    lines.append(f"**Уравнение:** {equation}")
    lines.append("")
    lines.append(_chip_diagram(pos_roles, neg_roles, target_name, b1))
    lines.append("")

    verify_header = (
        "| x₀ | x₁ | " + " | ".join(f"h{k}" for k in range(hidden_n)) + " | net | p | target | OK |"
    )
    verify_sep = "| " + " | ".join([":---:"] * (4 + hidden_n + 3)) + " |"
    verify_table = [verify_header, verify_sep]

    all_ok = True
    for xi, xj in cases:
        h_vals = [act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) for k in range(hidden_n)]
        net = sum(w1[k] * h_vals[k] for k in range(hidden_n)) + b1
        p = sigmoid(net)
        target = target_table[(xi, xj)]
        ok = round(p) == target
        all_ok = all_ok and ok
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        h_str = " | ".join(f"{v:+.3f}" for v in h_vals)
        verify_table.append(
            f"| {xi} | {xj} | {h_str} | {net:+.3f} | {p:.1%} | {target} | {mark} |"
        )
    lines.extend(verify_table)
    lines.append("")

    lines.append("### ШАГ 3. Вердикт")
    lines.append("")
    if all_ok:
        lines.append("⚖️ [green]**Сеть собрала верный чип XNOR.**[/green]")
        parts: list[str] = []
        for r in pos_roles:
            parts.append(f"**{r}**")
        for r in neg_roles:
            parts.append(f"**NOT {r}**")
        if parts:
            lines.append(
                f"Вентили {', '.join(parts)} объединяются через [ OR ] в выход {target_name}."
            )
        else:
            lines.append("Сеть решает задачу через выходной bias.")
    else:
        lines.append("⚖️ [red]**Схема неверна — сеть не собрала XNOR.**[/red]")

    if ignored:
        lines.append("")
        lines.append(
            f"[yellow]Примечание:[/yellow] нейроны h{', h'.join(str(k) for k in ignored)} "
            "не участвуют в схеме."
        )

    lines.append("")
    lines.append("(Esc — назад, 1 — default, 2 — forensic, 3 — prune)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Движок 2 — следовательский (forensic), основан на универсальном скрипте
# ---------------------------------------------------------------------------

# Все 16 возможных масок на 4 тестовых входах.
# Значение: (английский термин, русское описание).
_ROLE_NAMES: dict[tuple[int, ...], tuple[str, str]] = {
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

# Булева формула роли через входы x₀, x₁.
_ROLE_FORMULAS: dict[tuple[int, ...], str] = {
    (0, 0, 0, 0): "0",
    (1, 1, 1, 1): "1",
    (0, 1, 1, 1): "x₀ ∨ x₁",
    (0, 0, 0, 1): "x₀ ∧ x₁",
    (1, 1, 1, 0): "¬(x₀ ∧ x₁)",
    (1, 0, 0, 0): "¬x₀ ∧ ¬x₁",
    (0, 1, 1, 0): "(x₀ ∨ x₁) ∧ ¬(x₀ ∧ x₁)",
    (1, 0, 0, 1): "x₀ ↔ x₁",
    (0, 0, 1, 1): "x₀",
    (0, 1, 0, 1): "x₁",
    (1, 1, 0, 0): "¬x₀",
    (1, 0, 1, 0): "¬x₁",
    (0, 0, 1, 0): "x₀ ∧ ¬x₁",
    (0, 1, 0, 0): "x₁ ∧ ¬x₀",
    (1, 1, 0, 1): "x₀ → x₁",
    (1, 0, 1, 1): "x₁ → x₀",
}


def _mask_to_formula(mask: tuple[int, ...]) -> str:
    """Превратить маску активации в булеву формулу от x₀, x₁."""
    if mask in _ROLE_FORMULAS:
        return _ROLE_FORMULAS[mask]
    # Для неизвестной маски строим ДНФ по четырём входам.
    terms = []
    x_labels = (("¬x₀", "x₀"), ("¬x₁", "x₁"))
    for (xi, xj), bit in zip([(0, 0), (0, 1), (1, 0), (1, 1)], mask):
        if bit:
            terms.append(f"{x_labels[0][xi]} ∧ {x_labels[1][xj]}")
    if not terms:
        return "0"
    if len(terms) == 4:
        return "1"
    return " ∨ ".join(f"({t})" for t in terms)


def _join_formula_terms(terms: list[str], op: str) -> str:
    """Соединить термы оператором op (∨ или ∧)."""
    if not terms:
        return ""
    if len(terms) == 1:
        return terms[0]
    wrapped: list[str] = []
    for t in terms:
        if any(c in t for c in ("∧", "∨", "→", "↔")):
            wrapped.append(f"({t})")
        else:
            wrapped.append(t)
    return f" {op} ".join(wrapped)


def _to_code_formula(formula: str) -> str:
    """Перевести научную булеву формулу в синтаксис C++/JavaScript."""
    code = formula

    def _impl(m: re.Match) -> str:
        return f"(!{m.group(1).strip()} || {m.group(2).strip()})"

    def _equiv(m: re.Match) -> str:
        return f"({m.group(1).strip()} == {m.group(2).strip()})"

    # Разворачиваем импликацию и эквивалентность, возможно в скобках.
    code = re.sub(r"\(([^()]+)\)\s*→\s*\(([^()]+)\)", _impl, code)
    code = re.sub(r"\(([^()]+)\)\s*→\s*([^()]+)", _impl, code)
    code = re.sub(r"([^()]+)\s*→\s*\(([^()]+)\)", _impl, code)
    code = re.sub(r"([^()]+)\s*→\s*([^()]+)", _impl, code)

    code = re.sub(r"\(([^()]+)\)\s*↔\s*\(([^()]+)\)", _equiv, code)
    code = re.sub(r"\(([^()]+)\)\s*↔\s*([^()]+)", _equiv, code)
    code = re.sub(r"([^()]+)\s*↔\s*\(([^()]+)\)", _equiv, code)
    code = re.sub(r"([^()]+)\s*↔\s*([^()]+)", _equiv, code)

    code = code.replace("x₀", "x0")
    code = code.replace("x₁", "x1")
    code = code.replace("¬", "!")
    code = code.replace("∧", "&&")
    code = code.replace("∨", "||")
    return code


def _mask_to_truth(mask: tuple[int, ...]) -> dict[tuple[int, int], int]:
    """Маска (00,01,10,11) → словарь значений по входам."""
    return {
        (0, 0): mask[0],
        (0, 1): mask[1],
        (1, 0): mask[2],
        (1, 1): mask[3],
    }


def _evaluate_formula(
    pos_masks: list[tuple[int, ...]],
    neg_masks: list[tuple[int, ...]],
    target: dict[tuple[int, int], int],
) -> tuple[dict[tuple[int, int], int], bool]:
    """Вычислить формулу (∨ pos) ∧ ¬(∨ neg) и сравнить с target.

    Возвращает (answer_table, is_correct).
    """
    answer: dict[tuple[int, int], int] = {}
    for case in [(0, 0), (0, 1), (1, 0), (1, 1)]:
        pos_val = any(_mask_to_truth(m)[case] for m in pos_masks) if pos_masks else 1
        neg_val = any(_mask_to_truth(m)[case] for m in neg_masks) if neg_masks else 0
        answer[case] = 1 if pos_val and not neg_val else 0
    is_correct = all(answer[c] == target[c] for c in answer)
    return answer, is_correct


_BIAS_MASK: tuple[int, ...] = (1, 1, 1, 1)


def _build_logic_equation(
    effective_pos: list[int],
    effective_neg: list[int],
    masks: dict[int, tuple[int, ...]],
    target: dict[tuple[int, int], int],
    bias: float,
    bias_threshold: float = 1.0,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], bool, str]:
    """Подобрать набор ролей + bias, точно решающий target.

    Уравнение: (∨ pos_masks) ∧ ¬(∨ neg_masks) = target.
    Если bias по модулю превышает bias_threshold, добавляем виртуальную
    постоянную роль (1,1,1,1) в соответствующую часть:
      bias > +threshold  → pos (сеть по умолчанию выдаёт 1)
      bias < -threshold  → neg (сеть по умолчанию выдаёт 0)

    Возвращает (pos_masks, neg_masks, exact_match, bias_note).
    """
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

    # Если нет ни позитивных, ни негативных ролей — только bias.
    if not pos_masks and not neg_masks:
        return [], [], False, ""

    # Перебираем подмножества pos и neg, ищем точное совпадение.
    from itertools import combinations

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


def forensic_report(model_key: str, body: str) -> str | None:
    """Следовательский разбор в формате Markdown-отчёта."""
    parsed = _extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b2 = parsed

    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    act, act_name = _hidden_activation(model_key)

    # --- Роли скрытых нейронов ---
    roles: dict[int, tuple[str, str]] = {}
    masks: dict[int, tuple[int, ...]] = {}
    for k in range(hidden_n):
        outs = []
        for xi, xj in cases:
            z = xi * w0[0][k] + xj * w0[1][k] + b0[k]
            outs.append(1 if act(z) > 0.5 else 0)
        mask = tuple(outs)
        masks[k] = mask
        roles[k] = _ROLE_NAMES.get(mask, ("CUSTOM", f"Кастомный триггер {list(mask)}"))

    # --- Логическая формула ---
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

    pos_formulas = [_mask_to_formula(m) for m in real_pos]
    neg_formulas = [_mask_to_formula(m) for m in real_neg]
    pos_names = [_ROLE_NAMES.get(m, ("?", "?"))[0] for m in real_pos]
    neg_names = [_ROLE_NAMES.get(m, ("?", "?"))[0] for m in real_neg]

    pos_part = _join_formula_terms(pos_formulas, "∨")
    neg_part = _join_formula_terms(neg_formulas, "∨")

    if pos_part and neg_part:
        equation_scientific = f"({pos_part}) ∧ ¬({neg_part}) = {target_name}"
        equation_code = _to_code_formula(f"({pos_part}) && !({neg_part}) == true")
    elif pos_part:
        equation_scientific = f"{pos_part} = {target_name}"
        equation_code = _to_code_formula(f"{pos_part} == true")
    elif neg_part:
        equation_scientific = f"¬({neg_part}) = {target_name}"
        equation_code = _to_code_formula(f"!({neg_part}) == true")
    else:
        equation_scientific = f"? = {target_name}"
        equation_code = "? == true"

    # --- Проверка уравнения ---
    answer_table, equation_ok = _evaluate_formula(pos_masks, neg_masks, target_table)
    all_ok = equation_ok
    verification_rows = []
    for xi, xj in cases:
        case = (xi, xj)
        pos_vals = [_mask_to_truth(m)[case] for m in pos_masks]
        neg_vals = [_mask_to_truth(m)[case] for m in neg_masks]
        eq_val = answer_table[case]

        h_vals = [act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) for k in range(hidden_n)]
        net = sum(w1[k] * h_vals[k] for k in range(hidden_n)) + b2
        net_val = 1 if _sigmoid(net) > 0.5 else 0
        target = target_table[case]

        ok = eq_val == target == net_val
        all_ok = all_ok and ok
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"

        # Слагаемые в школьной записи: список "1" / "0" по позитивным ролям.
        terms = [str(int(v)) for v in pos_vals]
        if bias_pos:
            terms.append("1")
        terms_str = " + ".join(terms) if terms else "0"

        verification_rows.append((xi, xj, terms_str, eq_val, net_val, target, mark))

    # --- Вердикт: рабочие/бесполезные нейроны ---
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

    # --- Формирование отчёта ---
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

        neuron_cell = f"**h{k}**"
        role_table.append(
            f"| {neuron_cell} | {role_cell} | {meaning} | {weight:+.2f} | {status} |"
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

    # Бытовое описание ролей для вердикта.
    role_descriptions = []
    for k in active_pos:
        term = roles[k][0]
        role_descriptions.append(f"**{term}**")
    for k in active_neg:
        term = roles[k][0]
        role_descriptions.append(f"**{term}** (вычитание)")

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
    lines.append("(Esc — назад, 1 — default, 2 — forensic, 3 — prune)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Движок 3 — таблица prune-анализа
# ---------------------------------------------------------------------------


def prune_report(model_key: str, body: str) -> str | None:
    """Табличный отчёт: веса скрытых нейронов + вердикт KEEP/PRUNE."""
    parsed = _extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b2 = parsed

    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    act, act_name = _hidden_activation(model_key)

    keep_count = 0
    prune_count = 0

    lines: list[str] = []
    lines.append("### Таблица prune-анализа")
    lines.append("")
    lines.append(f"Архитектура: 2 входа → {hidden_n} скрытых ({act_name}) → 1 выход (sigmoid)")
    lines.append("")
    lines.append("Критерий PRUNE: нейрон мёртв (`max·activation < 0.1`) ИЛИ вес на выходе ≈ 0.")
    lines.append("")

    table_lines = [
        "| нейр | w_x0 | w_x1 | bias | w_out | max\\|act\\| | вердикт |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for k in range(hidden_n):
        wx0 = w0[0][k]
        wx1 = w0[1][k]
        bk = b0[k]
        wout = w1[k]

        h_vals = [act(wx0 * xi + wx1 * xj + bk) for xi, xj in cases]
        max_act = max(abs(v) for v in h_vals)

        is_dead = max_act < 0.1 or abs(wout) < 0.01
        if is_dead:
            prune_count += 1
            verdict = "[red]PRUNE[/red]"
        else:
            keep_count += 1
            verdict = "[green]KEEP[/green]"

        table_lines.append(
            f"| h{k} | {wx0:+.3f} | {wx1:+.3f} | {bk:+.3f} | {wout:+.3f} | {max_act:.3f} | {verdict} |"
        )

    # Строка выходного нейрона
    table_lines.append(
        "| out | — | — | {b2:+.3f} | — | — | [yellow]bias[/yellow] |".format(b2=b2)
    )

    lines.extend(table_lines)
    lines.append("")

    # Итог
    total = keep_count + prune_count
    if total == 0:
        lines.append("Нет скрытых нейронов для анализа.")
    else:
        lines.append(
            f"Итог: [green]{keep_count}[/green] из {total} — KEEP, [red]{prune_count}[/red] — PRUNE."
        )
        if prune_count > 0:
            lines.append("[yellow]Рекомендация:[/yellow] удалить [red]PRUNE[/red]-нейроны — сеть останется работоспособной.")
        else:
            lines.append("[green]Все нейроны задействованы, prune не требуется.[/green]")

    lines.append("")
    lines.append("(Esc — назад, 1 — default, 2 — forensic, 3 — prune)")

    return "\n".join(lines)
