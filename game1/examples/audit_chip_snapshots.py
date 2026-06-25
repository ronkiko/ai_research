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

    payload = {
        "total": len(items),
        "items": items,
        "errors": errors,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Audited {len(items)} snapshots; errors: {len(errors)}")
    print(f"Saved JSON to {RESULT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
