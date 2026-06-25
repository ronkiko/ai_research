"""Общие чистые функции для движков отчётов лаборатории.

Парсинг весов, извлечение двухслойной сети, бинаризация через pre-activation
threshold, классификация булевых масок и построение формул.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

# -----------------------------------------------------------------------------
# Константы бинаризации и статусов
# -----------------------------------------------------------------------------

EPS = 1e-6
"""Порог для ReLU: нейрон считаем активным, если z > EPS."""

STABLE_MARGIN = 1e-4
"""Если min(|z|) меньше этого значения, вентиль помечается unstable."""

WEIGHT_EPS = 1e-3
"""Вес на выходе, по модулю меньше которого, считается нулевым."""

# -----------------------------------------------------------------------------
# Роли и формулы
# -----------------------------------------------------------------------------

_ROLE_NAMES: dict[tuple[int, ...], tuple[str, str]] = {
    (0, 0, 0, 0): ("ZERO", "Constant 0"),
    (1, 1, 1, 1): ("ONE", "Constant 1"),
    (0, 1, 1, 1): ("OR", "x₀ ∨ x₁"),
    (0, 0, 0, 1): ("AND", "x₀ ∧ x₁"),
    (1, 1, 1, 0): ("NAND", "¬(x₀ ∧ x₁)"),
    (1, 0, 0, 0): ("NOR", "¬x₀ ∧ ¬x₁"),
    (0, 1, 1, 0): ("XOR", "(x₀ ∨ x₁) ∧ ¬(x₀ ∧ x₁)"),
    (1, 0, 0, 1): ("XNOR", "x₀ ↔ x₁"),
    (0, 0, 1, 1): ("PASS X0", "x₀"),
    (0, 1, 0, 1): ("PASS X1", "x₁"),
    (1, 1, 0, 0): ("NOT X0", "¬x₀"),
    (1, 0, 1, 0): ("NOT X1", "¬x₁"),
    (0, 0, 1, 0): ("X0 AND NOT X1", "x₀ ∧ ¬x₁"),
    (0, 1, 0, 0): ("X1 AND NOT X0", "x₁ ∧ ¬x₀"),
    (1, 1, 0, 1): ("X0 → X1", "x₀ → x₁"),
    (1, 0, 1, 1): ("X1 → X0", "x₁ → x₀"),
}

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


# -----------------------------------------------------------------------------
# Парсер весов из markdown-снапшота
# -----------------------------------------------------------------------------

_TABLE_HEADER_RE = re.compile(r"нейрон\s+(\d+)", re.IGNORECASE)
_SECOND_LAYER_RE = re.compile(r"(второго|second|выходного|output)", re.IGNORECASE)


def parse_weights(body: str) -> dict[str, float]:
    """Извлечь веса из тела .md-снапшота.

    Поддерживает два формата:
      - inline:  `0.weight[0]` = 5.5197
      - табличный:
            нейрон 0  нейрон 1  нейрон 2  нейрон 3
        weight[x0]   -0.257    -3.223    -0.697    +2.800
        weight[x1]   -0.269    -3.226    -0.652    +2.801
        bias         -0.225    +3.220    -0.256    -2.801
    """
    weights: dict[str, float] = {}

    # inline формат
    for line in body.splitlines():
        m = re.match(r"\s*`(.+?)`\s*=\s*([-\d.]+)", line)
        if m:
            try:
                weights[m.group(1)] = float(m.group(2))
            except ValueError:
                pass

    # табличный формат
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


# -----------------------------------------------------------------------------
# Извлечение параметров сети 2 → N → 1
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Parsed2Layer:
    hidden_n: int
    w0: list[list[float]]  # [input_idx][neuron]
    b0: list[float]        # bias скрытого слоя
    w1: list[float]        # веса на выход
    b2: float              # bias выхода


def extract_2layer_weights(model_key: str, body: str) -> Parsed2Layer | None:
    """Получить параметры сети 2 → N → 1 из снапшота.

    `model_key` пока не влияет на формат весов, но сохраняется для будущих
    специализаций. Возвращает `None`, если параметров нет.
    """
    del model_key  # reserved
    w = parse_weights(body)

    hidden_n = 0
    while f"0.bias[{hidden_n}]" in w:
        hidden_n += 1
    if hidden_n < 1:
        return None

    # inline формат PyTorch: 0.weight[0..{hidden_n*2-1}], чётные — x₀, нечётные — x₁.
    # Табличный формат: 0.weight[{k*2+in_idx}].
    inline_format = all(f"0.weight[{k}]" in w for k in range(hidden_n * 2))
    table_format = not inline_format and all(
        f"0.weight[{k * 2 + 0}]" in w or f"0.weight[{k * 2 + 1}]" in w
        for k in range(hidden_n)
    )
    if not (table_format or inline_format):
        return None

    w0: list[list[float]] = []
    for in_idx in range(2):
        w0.append([w.get(f"0.weight[{k * 2 + in_idx}]", 0.0) for k in range(hidden_n)])

    b0 = [w.get(f"0.bias[{k}]", 0.0) for k in range(hidden_n)]
    w1 = [w.get(f"2.weight[{k}]", 0.0) for k in range(hidden_n)]
    b2 = w.get("2.bias[0]", 0.0)

    return Parsed2Layer(hidden_n, w0, b0, w1, b2)


# -----------------------------------------------------------------------------
# Активация и бинаризация
# -----------------------------------------------------------------------------

def activation_family(model_key: str) -> tuple[str, Callable[[float], int]]:
    """Вернуть (название семейства, функцию бинаризации) для скрытого слоя.

    Все семейства, кроме ReLU, бинаризуются по `z >= 0`.
    ReLU бинаризуется по `z > EPS`.
    """
    if model_key == "mlp":
        return "ReLU", lambda z: 1 if z > EPS else 0
    if model_key == "torch":
        return "Tanh", lambda z: 1 if z >= 0 else 0
    return "Sigmoid", lambda z: 1 if z >= 0 else 0


def hidden_truth_table(
    w0: list[list[float]],
    b0: list[float],
    binarize: Callable[[float], int],
) -> tuple[list[tuple[float, float, float, float]], list[tuple[int, int, int, int]]]:
    """Для каждого скрытого нейрона посчитать z и mask на 00,01,10,11.

    Возвращает (z_values_per_neuron, masks_per_neuron).
    """
    cases = [(0, 0), (0, 1), (1, 0), (1, 1)]
    zvals: list[tuple[float, float, float, float]] = []
    masks: list[tuple[int, int, int, int]] = []
    for k in range(len(b0)):
        zs = [w0[0][k] * xi + w0[1][k] * xj + b0[k] for xi, xj in cases]
        mask = tuple(binarize(z) for z in zs)
        zvals.append(tuple(zs))  # type: ignore[arg-type]
        masks.append(mask)  # type: ignore[arg-type]
    return zvals, masks


def compute_margin(z_values: tuple[float, float, float, float]) -> float:
    """Минимальное расстояние от z до порога 0."""
    return min(abs(z) for z in z_values)


# -----------------------------------------------------------------------------
# Классификация масок и формулы
# -----------------------------------------------------------------------------

def classify_mask(mask: tuple[int, int, int, int]) -> tuple[str, str]:
    """Вернуть (role_key, human_description) для маски (00,01,10,11)."""
    return _ROLE_NAMES.get(mask, ("CUSTOM", f"custom {list(mask)}"))


def mask_to_formula(mask: tuple[int, ...]) -> str:
    """Превратить маску активации в булеву формулу от x₀, x₁."""
    if mask in _ROLE_FORMULAS:
        return _ROLE_FORMULAS[mask]
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


def join_formula_terms(terms: list[str], op: str) -> str:
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


def _impl_repl(m: re.Match) -> str:
    return f"(!{m.group(1).strip()} || {m.group(2).strip()})"


def _equiv_repl(m: re.Match) -> str:
    return f"({m.group(1).strip()} == {m.group(2).strip()})"


def to_code_formula(formula: str) -> str:
    """Перевести научную булеву формулу в синтаксис C++/JavaScript."""
    code = formula
    code = re.sub(r"\(([^()]+)\)\s*→\s*\(([^()]+)\)", _impl_repl, code)
    code = re.sub(r"\(([^()]+)\)\s*→\s*([^()]+)", _impl_repl, code)
    code = re.sub(r"([^()]+)\s*→\s*\(([^()]+)\)", _impl_repl, code)
    code = re.sub(r"([^()]+)\s*→\s*([^()]+)", _impl_repl, code)

    code = re.sub(r"\(([^()]+)\)\s*↔\s*\(([^()]+)\)", _equiv_repl, code)
    code = re.sub(r"\(([^()]+)\)\s*↔\s*([^()]+)", _equiv_repl, code)
    code = re.sub(r"([^()]+)\s*↔\s*\(([^()]+)\)", _equiv_repl, code)
    code = re.sub(r"([^()]+)\s*↔\s*([^()]+)", _equiv_repl, code)

    code = code.replace("x₀", "x0")
    code = code.replace("x₁", "x1")
    code = code.replace("¬", "!")
    code = code.replace("∧", "&&")
    code = code.replace("∨", "||")
    return code


# -----------------------------------------------------------------------------
# Утилиты верификации
# -----------------------------------------------------------------------------

def mask_to_truth(mask: tuple[int, ...]) -> dict[tuple[int, int], int]:
    """Маска (00,01,10,11) → словарь значений по входам."""
    return {
        (0, 0): mask[0],
        (0, 1): mask[1],
        (1, 0): mask[2],
        (1, 1): mask[3],
    }
