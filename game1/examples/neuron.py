#!/usr/bin/env python3
"""Эксперимент: прозвонка нейронов MLP через PyTorch forward hooks.

Загружает снапшот, реконструирует PyTorch-модель, прогоняет
00/01/10/11 и показывает реальные активации каждого нейрона.

Запуск:
  python3 examples/neuron.py            # REPL
  python3 examples/neuron.py probe            # активная модель
  python3 examples/neuron.py probe xnor       # с фильтром XNOR
  python3 examples/neuron.py probe <file>     # файл
  python3 examples/neuron.py probe <file> xor # файл + фильтр
"""
from __future__ import annotations

import atexit
import math
import readline
import sys
from pathlib import Path
from typing import Any
import torch
import torch.nn as nn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_HISTORY_FILE = Path.home() / ".neuron_history"

try:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    readline.read_history_file(str(_HISTORY_FILE))
except FileNotFoundError:
    pass
readline.set_history_length(500)
atexit.register(lambda: readline.write_history_file(str(_HISTORY_FILE)))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab.engines.common import parse_weights, classify_mask

console = Console()

_KNOWN_FILTERS: dict[str, tuple[int, int, int, int]] = {
    "zero":  (0, 0, 0, 0),
    "one":   (1, 1, 1, 1),
    "or":    (0, 1, 1, 1),
    "and":   (0, 0, 0, 1),
    "nand":  (1, 1, 1, 0),
    "nor":   (1, 0, 0, 0),
    "xor":   (0, 1, 1, 0),
    "xnor":  (1, 0, 0, 1),
}

_SCRIPT_DIR = Path(__file__).resolve().parent
_FIXTURE = str(_SCRIPT_DIR / "chip_fixtures" / "mlp_lie_detector_xnor_case_a.md")
_FIXTURE_LABEL = "mlp_lie_detector_xnor_case_a.md (fixture)"

_global_body: str | None = None

INPUTS = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)]


def _load_model(body: str) -> tuple[nn.Sequential, dict]:
    w = parse_weights(body)
    n = 0
    while f"0.bias[{n}]" in w:
        n += 1
    if n < 1:
        raise ValueError("нет скрытых нейронов (не 2→N→1)")

    w0 = [[w.get(f"0.weight[{k * 2 + in_idx}]", 0.0) for k in range(n)]
          for in_idx in range(2)]
    b0 = [w.get(f"0.bias[{k}]", 0.0) for k in range(n)]
    w1 = [w.get(f"2.weight[{k}]", 0.0) for k in range(n)]
    b2 = w.get("2.bias[0]", 0.0)

    model = nn.Sequential(
        nn.Linear(2, n), nn.ReLU(), nn.Linear(n, 1),
    )
    with torch.no_grad():
        model[0].weight.copy_(torch.tensor([[w0[0][k], w0[1][k]] for k in range(n)]))
        model[0].bias.copy_(torch.tensor(b0))
        model[2].weight.copy_(torch.tensor(w1).view(1, -1))
        model[2].bias.copy_(torch.tensor([b2]))
    model.eval()
    return model, {"b0": b0, "w1": w1, "b2": b2}


def _run_forward(model: nn.Sequential, n: int) -> tuple[list[list[float]], list[list[float]], list[float], list[int]]:
    z_vals: list[list[float]] = []
    h_vals: list[list[float]] = []

    def hook_z(m, i, o):
        z_vals.append(o[0].detach().tolist())

    def hook_h(m, i, o):
        h_vals.append(o[0].detach().tolist())

    handles = [
        model[0].register_forward_hook(hook_z),
        model[1].register_forward_hook(hook_h),
    ]

    logits: list[float] = []
    outputs: list[int] = []
    with torch.no_grad():
        for x0, x1 in INPUTS:
            inp = torch.tensor([[x0, x1]], dtype=torch.float32)
            out = model(inp)
            logits.append(out.item())
            outputs.append(1 if out.item() >= 0.0 else 0)

    for h in handles:
        h.remove()

    return z_vals, h_vals, logits, outputs


# ── styles ──

_STYLE_HEAD = "bold yellow"
_STYLE_LABEL = "cyan"
_STYLE_DIM = "dim white"
_STYLE_OK = "green"
_STYLE_FAIL = "red"
_STYLE_VIRTUAL = "magenta"
_STYLE_BOLD = "bold white"
_STYLE_WARN = "yellow"


def _style_for_act(v: float) -> str:
    return _STYLE_BOLD if v != 0.0 else _STYLE_DIM


def _style_for_logit(v: float) -> str:
    return _STYLE_OK if v >= 0 else _STYLE_FAIL


def _cell(text: str, style: str = "") -> str:
    return f"[{style}]{text}[/]" if style else text


def _neuron_status(h_k: list[float], role: str, w: float) -> Text:
    if all(v == 0.0 for v in h_k):
        return Text("dead (always 0)", style=_STYLE_DIM)
    if role in ("PASS X0", "PASS X1"):
        gate = role.split()[-1]
        style = _STYLE_FAIL if w < 0 else _STYLE_OK
        label = "inhibitory — vetoes" if w < 0 else "excitatory — reinforces"
        return Text(f"{label} when {gate}=1", style=style)
    mapper = {
        "AND": Text("AND detector — fires only when both inputs 1", style=_STYLE_OK),
        "OR": Text("OR detector — fires when any input 1", style=_STYLE_OK),
        "NAND": Text("NAND gate", style=_STYLE_WARN),
        "NOR": Text("NOR gate", style=_STYLE_WARN),
    }
    if role in mapper:
        return mapper[role]
    return Text(f"mixed role {role}", style=_STYLE_WARN)


def probe_snapshot(body: str, label: str = "", expect: tuple[int, int, int, int] | None = None) -> list[Any]:
    model, params = _load_model(body)
    n = len(params["b0"])
    b2 = params["b2"]
    z_vals, h_vals, logits, outputs = _run_forward(model, n)

    out_mask = tuple(outputs)
    out_role, _ = classify_mask(out_mask)

    renders: list[Any] = []

    # ── Header ──
    renders.append(Text(f"File: {label or '(inline)'}", style=_STYLE_LABEL))
    renders.append(
        Text.assemble(
            ("Model: MLP (ReLU)", _STYLE_LABEL),
            f", {n} hidden neurons, ",
            ("bias: ", _STYLE_LABEL),
            (f"{b2:+.4f}", _STYLE_DIM),
        )
    )
    renders.append("")

    # ── Full forward table ──
    table = Table(
        title="Full forward table",
        header_style=_STYLE_DIM,
        border_style=_STYLE_DIM,
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("x0 x1", justify="center", style=_STYLE_LABEL)
    for k in range(n):
        table.add_column(f"h{k}(z→act)", justify="center")
    table.add_column("logit", justify="right")
    table.add_column("out", justify="center")

    for i, (x0, x1) in enumerate(INPUTS):
        row: list[str] = [f"{int(x0)} {int(x1)}"]
        for k in range(n):
            act = h_vals[i][k]
            style = _style_for_act(act)
            row.append(_cell(f"{z_vals[i][k]:+.2f}→{act:.2f}", style))
        logit_style = _style_for_logit(logits[i])
        out_style = _style_for_logit(outputs[i])
        row.append(_cell(f"{logits[i]:+.2f}", logit_style))
        row.append(_cell(str(outputs[i]), out_style))
        table.add_row(*row)

    renders.append(Panel(table, border_style=_STYLE_DIM))
    renders.append("")

    # ── Per-neuron analysis ──
    neuron_panels: list[Panel] = []
    for k in range(n):
        z_k = [z_vals[i][k] for i in range(4)]
        h_k = [h_vals[i][k] for i in range(4)]
        mask = tuple(1 if v > 0.0 else 0 for v in h_k)
        role, _ = classify_mask(mask)
        contrib = [h_k[i] * params["w1"][k] for i in range(4)]

        w_style = _STYLE_OK if params["w1"][k] > 0 else _STYLE_WARN
        w_label = "positive" if params["w1"][k] > 0 else "negative"

        lines = Text.assemble(
            (f"h{k}: w_out = ", _STYLE_LABEL),
            (f"{params['w1'][k]:+.4f} ({w_label})", w_style),
            "\n",
            ("z: ", _STYLE_LABEL),
            f"[{', '.join(f'{v:+.3f}' for v in z_k)}]",
            "\n",
            ("ReLU: ", _STYLE_LABEL),
            f"[{', '.join(f'{v:.3f}' for v in h_k)}]",
            "\n",
            ("mask: ", _STYLE_LABEL),
            f"{mask} → {role}",
            "\n",
            ("status: ", _STYLE_LABEL),
        )
        lines.append_text(_neuron_status(h_k, role, params["w1"][k]))
        lines.append("\n")
        lines.append(Text(f"contribution (w·h): [{', '.join(f'{v:+.3f}' for v in contrib)}]", style=_STYLE_DIM))

        neuron_panels.append(Panel(lines, border_style=_STYLE_DIM, title=f"h{k}", title_align="left"))

    renders.append(Text("Per-neuron analysis", style=_STYLE_HEAD))
    renders.append("")
    for p in neuron_panels:
        renders.append(p)
    renders.append("")

    # ── Logit breakdown table ──
    lb_table = Table(
        title="Logit breakdown (bias + ∑ w·h)",
        header_style=_STYLE_DIM,
        border_style=_STYLE_DIM,
        padding=(0, 1),
    )
    lb_table.add_column("case", justify="center", style=_STYLE_LABEL)
    columns = [f"w{k}·h{k}" for k in range(n)]
    for c in columns:
        lb_table.add_column(c, justify="right")
    lb_table.add_column("=", justify="center")
    lb_table.add_column("logit", justify="right")

    for i, (x0, x1) in enumerate(INPUTS):
        row = [f"{int(x0)}{int(x1)}"]
        for k in range(n):
            row.append(f"{params['w1'][k] * h_vals[i][k]:+.4f}")
        total = b2 + sum(params["w1"][k] * h_vals[i][k] for k in range(n))
        style = _style_for_logit(total)
        row.append("=")
        row.append(_cell(f"{total:+.4f}", style))
        lb_table.add_row(*row)

    renders.append(Panel(lb_table, border_style=_STYLE_DIM))
    renders.append("")

    # ── Decoded strategy ──
    active_inhib: list[Text] = []
    active_excit: list[Text] = []
    for k in range(n):
        h_k = [h_vals[i][k] for i in range(4)]
        if all(v == 0.0 for v in h_k):
            continue
        mask = tuple(1 if v > 0.0 else 0 for v in h_k)
        role, _ = classify_mask(mask)
        txt = Text(f"h{k}({role}, w={params['w1'][k]:+.2f})")
        if params["w1"][k] < 0:
            txt.stylize(_STYLE_FAIL)
            active_inhib.append(txt)
        else:
            txt.stylize(_STYLE_OK)
            active_excit.append(txt)

    decoded = Text.assemble(
        ("bias: ", _STYLE_LABEL),
        (f"{b2:+.2f} (always ON)", _STYLE_OK if b2 > 0 else _STYLE_FAIL),
    )
    if b2 > 0:
        decoded.append(" → default output 1", style=_STYLE_OK)
    decoded.append("\n")

    if active_inhib:
        decoded.append(Text.assemble(("inhibitory: ", _STYLE_LABEL)))
        for i, t in enumerate(active_inhib):
            if i > 0:
                decoded.append(", ")
            decoded.append(t)
        decoded.append("\n")
    if active_excit:
        decoded.append(Text.assemble(("excitatory: ", _STYLE_LABEL)))
        for i, t in enumerate(active_excit):
            if i > 0:
                decoded.append(", ")
            decoded.append(t)
        decoded.append("\n")
    decoded.append(Text("\n"))
    if expect is not None:
        if out_mask == expect:
            decoded.append(Text.assemble(
                (f"Result: {out_mask} → ", "bold"),
                (f"{out_role}", _STYLE_OK),
                (f" ✓ matches expected", _STYLE_OK),
            ))
        else:
            exp_role, _ = classify_mask(expect)
            decoded.append(Text.assemble(
                (f"Result: {out_mask} → ", "bold"),
                (f"{out_role}", _STYLE_FAIL),
                (f" ✗ expected {exp_role}", _STYLE_WARN),
            ))
    else:
        decoded.append(Text.assemble(
            (f"Result: {out_mask} → ", "bold"),
            (f"{out_role}", _STYLE_OK),
        ))

    renders.append(Panel(decoded, title="Decoded: how the network solves the function", border_style=_STYLE_DIM))

    return renders


# ── Snapshot generation ──

def _generate_snapshot(arch: str = "") -> str:
    import re
    n = 4
    m = re.match(r"2[_-]?(\d+)[_-]?1", arch.replace(" ", ""))
    if m:
        n = int(m.group(1))
    if n < 1:
        n = 4

    rng = torch.Generator()
    rng.manual_seed(torch.seed() % (2**31))

    lim0 = 1.0 / math.sqrt(2.0)
    lim2 = 1.0 / math.sqrt(float(n))

    w0_flat = [torch.empty(1).uniform_(-lim0, lim0, generator=rng).item() for _ in range(n * 2)]
    b0 = [torch.empty(1).uniform_(-lim0, lim0, generator=rng).item() for _ in range(n)]
    w1 = [torch.empty(1).uniform_(-lim2, lim2, generator=rng).item() for _ in range(n)]
    b2 = torch.empty(1).uniform_(-lim2, lim2, generator=rng).item()

    lines = [
        "## Snapshot — Виртуальная модель",
        "",
        "- **Модель:** mlp (Нейросеть (MLP))",
        "- **Игра:** virtual",
        "- **Режим:** supervised",
        "",
        "### Веса",
        "",
    ]
    for k in range(n):
        lines.append(f"  `0.weight[{k * 2}]` = {w0_flat[k * 2]}")
        lines.append(f"  `0.weight[{k * 2 + 1}]` = {w0_flat[k * 2 + 1]}")
    for k in range(n):
        lines.append(f"  `0.bias[{k}]` = {b0[k]}")
    for k in range(n):
        lines.append(f"  `2.weight[{k}]` = {w1[k]}")
    lines.append(f"  `2.bias[0]` = {b2}")
    lines.append("")
    return "\n".join(lines)


# ── Commands ──

def cmd_probe(args: list[str]) -> int:
    expect: tuple[int, int, int, int] | None = None
    file_args = list(args)

    if args:
        raw_last = args[-1].lower()
        if raw_last in _KNOWN_FILTERS:
            expect = _KNOWN_FILTERS[raw_last]
            file_args = args[:-1]

    try:
        body, label = _resolve_body(file_args)
    except FileNotFoundError as e:
        if file_args:
            last = file_args[-1]
            # looks like a filter name (no extension, no path separators)
            if expect is None and not any(c in last for c in "./\\~"):
                msg = (
                    f"Неизвестный фильтр: [bold]{last}[/]. "
                    f"Доступны: {', '.join(_KNOWN_FILTERS)}"
                )
            else:
                msg = f"Файл не найден: [{_STYLE_BOLD}]{e}[/]"
        else:
            msg = (
                f"Неизвестный фильтр: [bold]{args[-1]}[/]. "
                f"Доступны: {', '.join(_KNOWN_FILTERS)}"
            )
        console.print(f"[{_STYLE_FAIL}]✗[/] {msg}")
        return 1

    try:
        _load_model(body)
    except (ValueError, KeyError, IndexError) as e:
        console.print(f"[{_STYLE_FAIL}]✗[/] Модель несовместима с probe: {e}")
        return 1

    console.print(f"[{_STYLE_HEAD}]=== NEURON PROBE ===[/]")
    for r in probe_snapshot(body, label=label, expect=expect):
        console.print(r)
    return 0


def cmd_new(args: list[str]) -> int:
    global _global_body
    arch = args[0] if args else ""
    _global_body = _generate_snapshot(arch)
    m = __import__("re").match(r"2[_-]?(\d+)[_-]?1", arch.replace(" ", ""))
    n_str = m.group(1) if m else "4"
    label = f"virtual MLP 2-{n_str}-1" if arch else "virtual MLP 2-4-1"
    console.print(f"[{_STYLE_OK}]✓[/] Создана [{_STYLE_VIRTUAL}]{label}[/]")
    return 0


def _current_source() -> str:
    if _global_body is not None:
        return f"[{_STYLE_VIRTUAL}]virtual (in-memory)[/]"
    return f"[{_STYLE_DIM}]file: {_FIXTURE}[/]"


def _resolve_body(args: list[str]) -> tuple[str, str]:
    global _global_body
    if args:
        path = Path(args[0])
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path.read_text(encoding="utf-8"), str(path)
    if _global_body is not None:
        return _global_body, "(virtual)"
    return Path(_FIXTURE).read_text(encoding="utf-8"), _FIXTURE_LABEL


def cmd_model(args: list[str]) -> int:
    """model info / model weights"""
    if not args:
        console.print(f"[{_STYLE_FAIL}]✗[/] Укажите подкоманду: [bold]info[/] или [bold]weights[/]")
        return 1

    sub = args[0]
    rest = args[1:]

    try:
        body, label = _resolve_body(rest)
    except FileNotFoundError as e:
        console.print(f"[{_STYLE_FAIL}]✗[/] Файл не найден: [{_STYLE_BOLD}]{e}[/]")
        return 1

    w = parse_weights(body)
    n = 0
    while f"0.bias[{n}]" in w:
        n += 1
    if n < 1:
        console.print(f"[{_STYLE_FAIL}]✗[/] Не удалось разобрать архитектуру")
        return 1

    n_params = 2 * n + n + n + 1  # w0 + b0 + w1 + b2

    if sub == "info":
        tbl = Table(title=f"Model info — {label}", border_style=_STYLE_DIM, header_style=_STYLE_LABEL)
        tbl.add_column("Layer", style=_STYLE_LABEL)
        tbl.add_column("Shape", justify="right")
        tbl.add_column("Params", justify="right")
        tbl.add_row("input", "2", "—")
        tbl.add_row(f"hidden (ReLU)", f"2×{n}", str(2 * n + n))
        tbl.add_row("output", f"{n}×1", str(n + 1))
        tbl.add_row("", "", "")
        tbl.add_row("[bold]Total[/]", "", f"[bold]{n_params}[/]")
        console.print(tbl)

    elif sub == "weights":
        w0 = [[w.get(f"0.weight[{k * 2 + in_idx}]", 0.0) for k in range(n)]
              for in_idx in range(2)]
        b0 = [w.get(f"0.bias[{k}]", 0.0) for k in range(n)]
        w1 = [w.get(f"2.weight[{k}]", 0.0) for k in range(n)]
        b2 = w.get("2.bias[0]", 0.0)

        console.print(Text.assemble(
            ("Weights — ", _STYLE_HEAD),
            (label, _STYLE_LABEL),
        ))

        tbl0 = Table(title=f"Layer 0: Linear(2, {n}) + ReLU", border_style=_STYLE_DIM, header_style=_STYLE_LABEL)
        tbl0.add_column("Neuron", style=_STYLE_LABEL)
        tbl0.add_column("w[x₀]", justify="right")
        tbl0.add_column("w[x₁]", justify="right")
        tbl0.add_column("bias", justify="right")
        for k in range(n):
            tbl0.add_row(f"h{k}", f"{w0[0][k]:+.4f}", f"{w0[1][k]:+.4f}", f"{b0[k]:+.4f}")
        console.print(Panel(tbl0, border_style=_STYLE_DIM))

        tbl2 = Table(title=f"Layer 2: Linear({n}, 1)", border_style=_STYLE_DIM, header_style=_STYLE_LABEL)
        tbl2.add_column("Source", style=_STYLE_LABEL)
        tbl2.add_column("weight", justify="right")
        for k in range(n):
            tbl2.add_row(f"h{k}", f"{w1[k]:+.4f}")
        tbl2.add_row("[dim]bias[/]", f"[dim]{b2:+.4f}[/]")
        console.print(Panel(tbl2, border_style=_STYLE_DIM))

    else:
        console.print(f"[{_STYLE_FAIL}]✗[/] Неизвестная подкоманда: [bold]{sub}[/]. Используйте [bold]info[/] или [bold]weights[/]")
        return 1

    return 0


_STEPS_PER_PASS = 10_000


def _play_ball(passes: int = 1) -> int:
    from modules.core.mechanics.level0.ball import BallMechanics

    try:
        body, label = _resolve_body([])
    except FileNotFoundError as e:
        console.print(f"[{_STYLE_FAIL}]✗[/] Файл не найден: [{_STYLE_BOLD}]{e}[/]")
        return 1
    try:
        model, _ = _load_model(body)
    except (ValueError, KeyError, IndexError) as e:
        console.print(f"[{_STYLE_FAIL}]✗[/] Модель несовместима: {e}")
        return 1

    game = BallMechanics()
    game.sit()
    total_steps = _STEPS_PER_PASS * passes

    import time
    t0 = time.monotonic()
    correct = 0
    total_reward = 0

    label_p = f" ({passes} проходов)" if passes > 1 else ""
    console.print(f"[{_STYLE_HEAD}]=== PLAY: ball{label_p} ===[/]")
    console.print(f"[{_STYLE_DIM}]Играет модель: {label}[/]")

    from rich.progress import Progress
    with Progress(console=console) as progress:
        task = progress.add_task("Идёт игра...", total=total_steps)
        for _ in range(passes):
            game.sit()
            for step in range(_STEPS_PER_PASS):
                game.observe()
                inp = torch.tensor([[0.0, 0.0]], dtype=torch.float32)
                out = model(inp)
                action = 1 if out.item() >= 0.0 else 0
                result = game.step(action)
                total_reward += result.reward
                if result.reward == 1:
                    correct += 1
                if step % 100 == 0:
                    progress.update(task, advance=100)

    elapsed = time.monotonic() - t0
    speed = total_steps / elapsed
    console.print()
    console.print(f"[{_STYLE_HEAD}]Результаты:[/]")
    console.print(f"  Всего ходов:       {total_steps}")
    console.print(f"  Угадано:           {correct} / {total_steps}")
    console.print(f"  Точность:          {correct / total_steps * 100:.1f}%")
    console.print(f"  Суммарная награда:  {total_reward}")
    console.print(f"  Время:             {elapsed:.2f} с")
    console.print(f"  Скорость:          {speed:,.0f} шаг/с")
    return 0


def cmd_play(args: list[str]) -> int:
    if not args:
        from modules.core.casino import _TABLES
        tbl = Table(title="Доступные игры", border_style=_STYLE_DIM, header_style=_STYLE_LABEL)
        tbl.add_column("Ключ", style=_STYLE_LABEL)
        tbl.add_column("Название", style=_STYLE_OK)
        tbl.add_column("Описание")
        for key, cls in _TABLES.items():
            tbl.add_row(key, cls.TITLE, cls.SUMMARY)
        console.print(tbl)
        return 0
    game = args[0]
    passes = 1
    if len(args) > 1 and args[1].startswith("x"):
        try:
            passes = int(args[1][1:])
        except ValueError:
            pass
    if game == "ball":
        return _play_ball(passes)
    console.print(f"[{_STYLE_HEAD}]=== PLAY: {game} ===[/]")
    console.print(f"[{_STYLE_DIM}]Игра «{game}» пока не реализована.[/]")
    return 0


def cmd_help() -> int:
    console.print(f"[{_STYLE_LABEL}]Источник:[/] {_current_source()}")
    console.print(f"[{_STYLE_HEAD}]Доступные команды:[/]")
    console.print(f"  [bold]probe[/] [[{_STYLE_DIM}]file[/]] [[{_STYLE_DIM}]filter[/]] — прозвонка нейронов (фильтр: {', '.join(_KNOWN_FILTERS)})")
    console.print(f"  [bold]new[/]   [[{_STYLE_DIM}]arch[/]]     — создать виртуальную модель (напр. 2-8-1)")
    console.print(f"  [bold]play[/]  [[{_STYLE_DIM}]game[/]]    — список игр / запустить игру")
    console.print(f"  [bold]model info[/]              — архитектура текущей модели")
    console.print(f"  [bold]model weights[/]           — веса текущей модели")
    console.print(f"  [bold]help[/]                    — это сообщение")
    console.print(f"  [bold]exit[/] / [bold]quit[/]             — выход")
    return 0


_COMMANDS = ["probe", "new", "model", "play", "help", "exit", "quit"]
_MODEL_SUB = ["info", "weights"]
_NEW_ARCHS = ["2-4-1", "2-8-1", "2-16-1"]


def _completer(text: str, state: int) -> str | None:
    line = readline.get_line_buffer()
    beg = readline.get_begidx()

    if beg == 0:
        options = [c for c in _COMMANDS if c.startswith(text)]
    else:
        parts = line.split()
        cmd = parts[0] if parts else ""
        if cmd == "model":
            options = [s for s in _MODEL_SUB if s.startswith(text)]
        elif cmd == "probe":
            options = [f for f in _KNOWN_FILTERS if f.startswith(text)]
        elif cmd == "new":
            options = [a for a in _NEW_ARCHS if a.startswith(text)]
        elif cmd == "play":
            from modules.core.casino import _TABLES
            options = [k for k in _TABLES if k.startswith(text)]
        else:
            options = []
    try:
        return options[state]
    except IndexError:
        return None


def repl() -> int:
    readline.set_completer(_completer)
    readline.parse_and_bind("tab: complete")
    console.print(f"[{_STYLE_HEAD}]neuron REPL[/]  [{_STYLE_LABEL}]Источник:[/] {_current_source()}")
    console.print(f"[{_STYLE_DIM}]Введите help для справки, exit для выхода.[/]")
    # readline-safe prompt: \001/\002 hide ANSI escapes from cursor tracking
    _PROMPT = f"\001\033[36m\002neuron\001\033[0m\002 "
    while True:
        try:
            line = input(_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0]
        rest = parts[1:]

        if cmd in ("exit", "quit"):
            break
        elif cmd == "help":
            cmd_help()
        elif cmd == "probe":
            cmd_probe(rest)
        elif cmd == "new":
            cmd_new(rest)
        elif cmd == "model":
            cmd_model(rest)
        elif cmd == "play":
            cmd_play(rest)
        else:
            console.print(f"[{_STYLE_FAIL}]✗[/] Неизвестная команда: [bold]{cmd}[/]. [{_STYLE_DIM}]Введите help.[/]")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        return repl()

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "probe":
        return cmd_probe(rest)
    if cmd == "new":
        return cmd_new(rest)
    if cmd == "model":
        return cmd_model(rest)
    if cmd == "play":
        return cmd_play(rest)
    if cmd == "help":
        return cmd_help()

    console.print(f"[{_STYLE_FAIL}]✗[/] Неизвестная подкоманда: [bold]{cmd}[/]. [{_STYLE_DIM}]Введите help.[/]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
