"""Пробежать chip-анализ по локальным markdown-снапшотам."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS_DIR = ROOT / "research" / "weights"
RESULT_PATH = ROOT / "examples" / "results" / "chip_audit.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.lab_engines.chip import analyze_chip, parse_snapshot_meta


def build_summary(items: list[dict[str, object]]) -> dict[str, object]:
    """Свести список item-ов в компактный summary для audit JSON."""
    known = [it for it in items if it.get("target_role") != "unknown"]
    matched = [it for it in known if it.get("match") is True]
    failed = [it for it in known if it.get("match") is False]
    unknown_target = [it for it in items if it.get("target_role") == "unknown"]

    by_accuracy: dict[str, dict[str, int]] = {}
    for it in items:
        acc = str(it.get("accuracy") or "unknown")
        bucket = by_accuracy.setdefault(acc, {"total": 0, "matched": 0, "failed": 0})
        bucket["total"] += 1
        if it.get("match") is True:
            bucket["matched"] += 1
        elif it.get("match") is False:
            bucket["failed"] += 1

    return {
        "total": len(items),
        "known_target_total": len(known),
        "matched": len(matched),
        "failed": len(failed),
        "unknown_target": len(unknown_target),
        "by_accuracy": by_accuracy,
    }


def main() -> int:
    items: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    for path in sorted(WEIGHTS_DIR.rglob("*.md")):
        body = path.read_text(encoding="utf-8")
        meta = parse_snapshot_meta(body)
        model = meta.model or path.parent.name
        analysis = analyze_chip(model, body)
        if analysis is None:
            errors.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "error": "unsupported snapshot for chip engine",
                }
            )
            continue

        items.append(
            {
                "path": str(path.relative_to(ROOT)),
                "model": analysis.snapshot.model or model,
                "game": analysis.snapshot.game or "unknown",
                "mode": analysis.snapshot.mode or "unknown",
                "accuracy": analysis.snapshot.accuracy or "unknown",
                "target_role": analysis.target_role or "unknown",
                "network_role": analysis.output_role,
                "match": analysis.real_network.solves_target,
                "cmos_transistors": analysis.cmos.functional.transistors,
            }
        )

    summary = build_summary(items)
    payload = {
        **summary,
        "items": items,
        "errors": errors,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Audited {summary['total']} snapshots; "
        f"matched: {summary['matched']}; "
        f"failed: {summary['failed']}; "
        f"unknown target: {summary['unknown_target']}; "
        f"errors: {len(errors)}"
    )
    print(f"Saved JSON to {RESULT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())