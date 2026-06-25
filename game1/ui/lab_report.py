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


def _infer_strategy(
    hidden_n: int,
    w0: list[list[float]],
    b0: list[float],
    w1: list[float],
    b1: float,
    act,
    act_name: str,
) -> list[str]:
    """Понять, как сеть решает XOR, и описать словами."""
    lines: list[str] = []
    inputs = [(0, 0), (0, 1), (1, 0), (1, 1)]

    neuron_on: list[list[tuple[int, int]]] = []
    for k in range(hidden_n):
        on = []
        for xi, xj in inputs:
            hv = act(w0[0][k] * xi + w0[1][k] * xj + b0[k])
            if abs(hv) > 0.5:
                on.append((xi, xj))
        neuron_on.append(on)

    for k in range(hidden_n):
        on = neuron_on[k]
        on_pos = [(xi, xj) for xi, xj in on if act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) > 0.5]
        on_neg = [(xi, xj) for xi, xj in on if act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) < -0.5]

        if not on_pos and not on_neg:
            lines.append(f"[red]h{k} — мёртв (всегда = {act(b0[k]):+.3f})[/red]")
            continue

        parts = []
        if len(on_pos) == 1:
            xi, xj = on_pos[0]
            parts.append(f"зажигается на ({xi},{xj})")
        elif len(on_pos) > 1:
            parts.append(f"горит на {', '.join(f'({xi},{xj})' for xi, xj in on_pos)}")

        if len(on_neg) == 1:
            xi, xj = on_neg[0]
            parts.append(f"гаснет на ({xi},{xj})")
        elif len(on_neg) > 1:
            parts.append(f"отрицателен на {', '.join(f'({xi},{xj})' for xi, xj in on_neg)}")

        lines.append(f"h{k}: {', '.join(parts)}")

    lines.append("")

    dead_neurons = [k for k in range(hidden_n) if abs(w1[k]) < 0.01]
    pos_neurons = [k for k in range(hidden_n) if w1[k] > 0 and k not in dead_neurons]
    neg_neurons = [k for k in range(hidden_n) if w1[k] < 0 and k not in dead_neurons]

    parts = []
    if pos_neurons:
        parts.append(f"[green]СКЛАДЫВАЕТ {', '.join(f'h{k}' for k in pos_neurons)}[/green]")
    if neg_neurons:
        parts.append(f"[yellow]ВЫЧИТАЕТ {', '.join(f'h{k}' for k in neg_neurons)}[/yellow]")
    if dead_neurons:
        parts.append(f"[red]игнорирует h{', h'.join(str(k) for k in dead_neurons)}[/red]")

    lines.append("Стратегия выхода: " + ", ".join(parts) + ".")
    lines.append(f"Bias выхода {b1:+.4f} — порог sigmoid.")
    lines.append("")

    # Проверка
    all_ok = True
    for xi, xj in inputs:
        h_vals = [act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) for k in range(hidden_n)]
        out_z = sum(w1[k] * h_vals[k] for k in range(hidden_n)) + b1
        target = 1 if xi == xj else 0
        if (out_z > 0) != (target == 1):
            all_ok = False

    if all_ok:
        lines.append("[green]Сеть решает XOR правильно на всех четырёх входах. ✓[/green]")
    else:
        lines.append("[red]Сеть ошибается на некоторых входах. ✗[/red]")

    return lines


def default_report(model_key: str, body: str) -> str | None:
    """Классический детальный разбор для torch/mlp."""
    if model_key not in ("torch", "mlp"):
        return None

    parsed = _extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b1 = parsed

    def act(v: float) -> float:
        if model_key == "torch":
            return math.tanh(v)
        return max(0.0, v)

    def sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    inputs = [(0, 0), (0, 1), (1, 0), (1, 1)]
    act_name = "Tanh" if model_key == "torch" else "ReLU"

    lines: list[str] = []
    lines.append("### Разбор нейронов")
    lines.append("")
    lines.append(f"Архитектура: 2 входа → {hidden_n} скрытых ({act_name}) → 1 выход")
    lines.append("")

    for k in range(hidden_n):
        w0k = w0[0][k]
        w1k = w0[1][k]
        bk = b0[k]
        lines.append(f"**Нейрон h{k}:** {act_name}({w0k:+.4f}·x₀ {w1k:+.4f}·x₁ {bk:+.4f})")
        desc = []
        for xi, xj in inputs:
            hv = act(w0k * xi + w1k * xj + bk)
            desc.append(f"({xi},{xj})→{hv:+.3f}")
        lines.append("  " + "  ".join(desc))
        lines.append("")

    lines.append("### Когда что горит")
    lines.append("")
    lines.extend(_infer_strategy(hidden_n, w0, b0, w1, b1, act, act_name))
    lines.append("")

    lines.append("### Выход (net → sigmoid → p)")
    lines.append("")
    out_parts = " ".join(f"{w1[k]:+.4f}·h{k}" for k in range(hidden_n))
    lines.append(f"net = {out_parts} {b1:+.4f}  → sigmoid → p")
    lines.append("")
    for xi, xj in inputs:
        h_vals = [act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) for k in range(hidden_n)]
        out_z = sum(w1[k] * h_vals[k] for k in range(hidden_n)) + b1
        out_p = sigmoid(out_z)
        target = 1 if xi == xj else 0
        h_str = ", ".join(f"h{k}={v:+.3f}" for k, v in enumerate(h_vals))
        arrow = "[green]✓[/green]" if round(out_p) == target else "[red]✗[/red]"
        lines.append(
            f"  ({xi},{xj}): {h_str} → net={out_z:+.3f} → p={out_p:.1%} (target={target}) {arrow}"
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
    """Следовательский разбор: роли, дубли, вердикт с учётом реальной активации."""
    parsed = _extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b2 = parsed

    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    act, act_name = _hidden_activation(model_key)

    lines: list[str] = []
    lines.append("### Следовательский разбор")
    lines.append("")
    lines.append(f"Архитектура: 2 входа → {hidden_n} скрытых ({act_name}) → 1 выход (sigmoid)")
    lines.append("")

    # --- Роли скрытых нейронов ---
    roles: dict[int, str] = {}
    masks: dict[int, tuple[int, ...]] = {}
    for k in range(hidden_n):
        outs = []
        for xi, xj in cases:
            z = xi * w0[0][k] + xj * w0[1][k] + b0[k]
            outs.append(1 if act(z) > 0.5 else 0)
        mask = tuple(outs)
        masks[k] = mask
        role_pair = _ROLE_NAMES.get(mask, ("CUSTOM", f"Кастомный триггер {list(mask)}"))
        roles[k] = role_pair

    # --- Таблица ролей ---
    lines.append("### Таблица ролей нейронов")
    lines.append("")

    col1 = 6   # нейрон
    col2 = 14  # англ. термин
    col3 = 38  # русское описание
    col4 = 7   # вес
    col5 = 9   # вклад

    top = (
        f"┌{'─' * col1}┬{'─' * col2}┬{'─' * col3}┬{'─' * col4}┬{'─' * col5}┐"
    )
    hdr = (
        f"│{'нейр':^{col1}}│{'роль (англ.)':^{col2}}│"
        f"{'смысл':^{col3}}│{'вес':^{col4}}│{'участие':^{col5}}│"
    )
    sep = (
        f"├{'─' * col1}┼{'─' * col2}┼{'─' * col3}┼{'─' * col4}┼{'─' * col5}┤"
    )
    bot = (
        f"└{'─' * col1}┴{'─' * col2}┴{'─' * col3}┴{'─' * col4}┴{'─' * col5}┘"
    )

    lines.append(top)
    lines.append(hdr)
    lines.append(sep)

    for k in range(hidden_n):
        weight = w1[k]
        mask = masks[k]
        term, meaning = roles[k]

        if abs(weight) < 0.01 or mask == (0, 0, 0, 0) or mask == (1, 1, 1, 1):
            color = "red"
            w_text = f"{weight:+.2f}"[:col4]
            part_text = "[red]игнор[/red]"
        elif weight > 0:
            color = "green"
            w_text = f"{weight:+.2f}"[:col4]
            part_text = "[green]+[/green]"
        else:
            color = "yellow"
            w_text = f"{weight:+.2f}"[:col4]
            part_text = "[yellow]-[/yellow]"

        term_text = f"[{color}]{term}[/{color}]"
        meaning_text = f"[{color}]{meaning}[/{color}]"

        row = (
            f"│{_center(f'h{k}', col1)}│{_center(term_text, col2)}│{_center(meaning_text, col3)}│"
            f"{_center(w_text, col4)}│{_center(part_text, col5)}│"
        )
        lines.append(row)

    lines.append(bot)
    lines.append("")

    # --- Логическая схема ---
    effective_pos = [
        k for k in range(hidden_n)
        if abs(w1[k]) >= 0.01 and masks[k] not in ((0, 0, 0, 0), (1, 1, 1, 1)) and w1[k] > 0
    ]
    effective_neg = [
        k for k in range(hidden_n)
        if abs(w1[k]) >= 0.01 and masks[k] not in ((0, 0, 0, 0), (1, 1, 1, 1)) and w1[k] < 0
    ]

    if effective_pos or effective_neg:
        lines.append("### Логическое уравнение")
        lines.append("")

        target_table = {c: (1 if c[0] == c[1] else 0) for c in cases}
        target_name = "XNOR"  # в игре lie_detector правда = показания совпали
        pos_masks, neg_masks, exact, bias_note = _build_logic_equation(
            effective_pos, effective_neg, masks, target_table, b2
        )

        # Разделяем виртуальную роль bias от реальных нейронов.
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
            equation_short = f"({pos_part}) + NOT ({neg_part})"
            equation_full = f"({pos_part}) ∧ ¬({neg_part})"
        elif pos_part:
            equation_short = pos_part
            equation_full = pos_part
        elif neg_part:
            equation_short = f"NOT ({neg_part})"
            equation_full = f"¬({neg_part})"
        else:
            equation_short = "?"
            equation_full = "?"

        lines.append(f"**Уравнение: {equation_short} = {target_name}**")
        lines.append(f"  → {equation_full}")
        lines.append("")

        notes: list[str] = []
        if pos_names:
            joined = ", ".join(f"[green]{n}[/green]" for n in pos_names)
            notes.append(f"позитивные роли: {joined}")
        if neg_names:
            joined = ", ".join(f"[yellow]{n}[/yellow]" for n in neg_names)
            notes.append(f"негативные роли: {joined}")
        if bias_pos or bias_neg:
            notes.append(f"[cyan]bias[/cyan] как постоянная {1 if bias_pos else 0}")
        if bias_note:
            notes.append(f"[cyan]{bias_note}[/cyan]")
        if notes:
            lines.append("Детектор выделил: " + "; ".join(notes) + ".")
        else:
            lines.append("[red]Детектор не выделил ни одной рабочей роли.[/red]")
        lines.append("")

        # --- Проверка уравнения ---
        answer_table, equation_ok = _evaluate_formula(pos_masks, neg_masks, target_table)

        headers = ["x₀", "x₁"]
        if pos_masks:
            headers.append(" ∨ ".join(f"p{i+1}" for i in range(len(pos_masks))))
        if neg_masks:
            headers.append(" ∧ ".join(f"n{i+1}" for i in range(len(neg_masks))))
        headers.extend(["уравнение", "сеть", "target", "ok"])
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join("---" for _ in headers) + "|")

        all_ok = equation_ok
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

            cells = [f"{xi}", f"{xj}"]
            if pos_masks:
                cells.append(" ∨ ".join(str(v) for v in pos_vals))
            if neg_masks:
                cells.append(" ∧ ".join(str(v) for v in neg_vals))
            cells.extend([f"{eq_val}", f"{net_val}", f"{target}", mark])
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

        if all_ok:
            lines.append(
                f"[green]Уравнение детектора верно: роли действительно складываются в {target_name}. ✓[/green]"
            )
        else:
            lines.append(
                "[red]Уравнение детектора неверно — либо детектор сглючил, либо сеть не решила задачу. ✗[/red]"
            )
        lines.append("")

        # Бытовое объяснение для распространённых уравнений.
        explanations: dict[str, str] = {
            "(¬x₀ ∧ ¬x₁) ∨ (x₀ ∧ x₁)":
                "«оба сказали нет» ИЛИ «оба сказали да» → показания совпали.",
            "(x₀ ∨ x₁) ∧ ¬(x₀ ∧ x₁)":
                "«хоть один сказал да» И НЕ «оба сказали да» → показания разошлись.",
            "(x₁ → x₀) ∧ ¬(x₀ ∧ ¬x₁)":
                "«второй ведёт к первому» И НЕ «только первый прав» → показания совпали.",
            "x₀ ↔ x₁":
                "первый и второй совпадают → показания совпали.",
            "¬((x₁ ∧ ¬x₀) ∨ (x₀ ∧ ¬x₁))":
                "НЕ «только один сказал да» → показания совпали.",
        }
        if equation_full in explanations:
            lines.append(f"**Смысл:** {explanations[equation_full]}")
            lines.append("")

        lines.append(
            "Классическое XOR (различие) строится как «OR + NOT AND»: "
            "`(x₀ ∨ x₁) ∧ ¬(x₀ ∧ x₁)`. "
            f"Текущая задача — {target_name}, это инверсия XOR."
        )
        lines.append("")

        if not exact:
            lines.append(
                "[yellow]Примечание:[/yellow] подобрано ближайшее приближение; возможно, "
                "детектору не хватило чистых OR/XOR-ролей."
            )
            lines.append("")

    # --- Вердикт: дубли и общая картина ---
    lines.append("### Вердикт следователя")
    lines.append("")
    lines.append("Сеть решает XOR как комбинация логических ролей нейронов.")
    lines.append("")

    seen_masks: dict[tuple[int, ...], int] = {}
    duplicates: list[tuple[int, int]] = []  # (current, previous)
    effective_pos: list[int] = []
    effective_neg: list[int] = []
    useless: list[int] = []

    for k in range(hidden_n):
        weight = w1[k]
        mask = masks[k]
        if abs(weight) < 0.01 or mask == (0, 0, 0, 0) or mask == (1, 1, 1, 1):
            useless.append(k)
        elif mask in seen_masks:
            duplicates.append((k, seen_masks[mask]))
        else:
            seen_masks[mask] = k
            if weight > 0:
                effective_pos.append(k)
            else:
                effective_neg.append(k)

    if effective_pos:
        lines.append(f"[green]Складывает паттерны: h{', h'.join(str(k) for k in effective_pos)}[/green]")
    if effective_neg:
        lines.append(f"[yellow]Вычитает паттерны: h{', h'.join(str(k) for k in effective_neg)}[/yellow]")
    if useless:
        lines.append(f"[red]Игнорирует бесполезных: h{', h'.join(str(k) for k in useless)}[/red]")
    if duplicates:
        for k, prev in duplicates:
            same_sign = (w1[k] > 0 and w1[prev] > 0) or (w1[k] < 0 and w1[prev] < 0)
            note = "работают заодно" if same_sign else "[red]гасят друг друга (противоположные веса)[/red]"
            lines.append(f"[yellow]Дубликат: h{k} делает то же, что h{prev} — {note}[/yellow]")
    lines.append("")

    # --- Таблица выхода ---
    lines.append("### Расчёт выхода")
    lines.append("")
    out_formula = " ".join(f"{w1[k]:+.4f}·h{k}" for k in range(hidden_n))
    lines.append(f"net = {out_formula} {b2:+.4f}")
    lines.append("sigmoid(net) → p, порог 0.5")
    lines.append("")
    all_ok = True
    for xi, xj in cases:
        h_vals = [act(w0[0][k] * xi + w0[1][k] * xj + b0[k]) for k in range(hidden_n)]
        net = sum(w1[k] * h_vals[k] for k in range(hidden_n)) + b2
        p = _sigmoid(net)
        target = 1 if xi == xj else 0
        ok = (p > 0.5) == (target == 1)
        all_ok = all_ok and ok
        h_str = ", ".join(f"h{k}={v:.3f}" for k, v in enumerate(h_vals))
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        lines.append(f"  ({xi},{xj}): {h_str} → net={net:+.3f} → p={p:.1%} (target={target}) {mark}")

    lines.append("")
    if all_ok:
        lines.append("[green]Сеть корректно решает XOR на всех четырёх входах. ✓[/green]")
    else:
        lines.append("[red]Сеть ошибается — следователь вынесет неверный вердикт. ✗[/red]")

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

    lines: list[str] = []
    lines.append("### Таблица prune-анализа")
    lines.append("")
    lines.append(f"Архитектура: 2 входа → {hidden_n} скрытых ({act_name}) → 1 выход (sigmoid)")
    lines.append("")
    lines.append("Критерий PRUNE: нейрон мёртв (max|activation| < 0.1) ИЛИ вес на выходе ≈ 0.")
    lines.append("")

    # Заголовок таблицы (поуже, чтобы влезать на 80×24)
    header = f"{'нейр':>4} │ {'w_x0':>7} │ {'w_x1':>7} │ {'bias':>7} │ {'w_out':>7} │ {'max|act|':>7} │ вердикт"
    lines.append(header)
    lines.append("─" * len(header))

    keep_count = 0
    prune_count = 0

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

        row = (
            f"{f'h{k}':>4} │ {wx0:7.3f} │ {wx1:7.3f} │ {bk:7.3f} │ {wout:7.3f} │ {max_act:7.3f} │ {verdict}"
        )
        lines.append(row)

    # Строка выходного нейрона
    lines.append("─" * len(header))
    lines.append(f"{'out':>4} │ {'—':>7} │ {'—':>7} │ {b2:7.3f} │ {'—':>7} │ {'—':>7} │ [yellow]bias[/yellow]")
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
