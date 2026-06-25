"""Движок `prune` — табличный анализ скрытых нейронов: KEEP / PRUNE.

Сохраняет поведение оригинального prune_report: нейрон мёртв, если его
максимальная активация мала или вес на выходе ≈ 0.
"""
from __future__ import annotations

from .base import ReportEngineInfo
from .common import (
    Parsed2Layer,
    activation_family as _new_activation_family,
    extract_2layer_weights,
)


def _hidden_activation(model_key: str):
    """Оригинальная активация prune: используем post-activation значение."""
    if model_key == "torch":
        import math
        return math.tanh, "Tanh"
    if model_key == "mlp":
        return lambda v: max(0.0, v), "ReLU"
    import math

    def _sigmoid(x: float) -> float:
        if x < -50:
            return 0.0
        if x > 50:
            return 1.0
        return 1.0 / (1.0 + math.exp(-x))

    return _sigmoid, "Sigmoid"


def prune_report(model_key: str, body: str) -> str | None:
    parsed = extract_2layer_weights(model_key, body)
    if parsed is None:
        return None
    hidden_n, w0, b0, w1, b2 = parsed.hidden_n, parsed.w0, parsed.b0, parsed.w1, parsed.b2

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

    table_lines.append(
        "| out | — | — | {b2:+.3f} | — | — | [yellow]bias[/yellow] |".format(b2=b2)
    )

    lines.extend(table_lines)
    lines.append("")

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
    lines.append("(Esc — назад, 1 — chip, 2 — forensic, 3 — prune)")

    return "\n".join(lines)


class PruneEngine:
    info = ReportEngineInfo(
        key="prune",
        hotkey="3",
        title="prune",
        summary="prune: табличный анализ KEEP/PRUNE скрытых нейронов",
    )

    @staticmethod
    def render(model_key: str, snapshot_body: str) -> str | None:
        return prune_report(model_key, snapshot_body)
