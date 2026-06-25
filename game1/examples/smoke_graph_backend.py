from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application import GameApplication


def main() -> None:
    app = GameApplication.create_default()

    print(app.select_game("lie_detector"))
    print(app.select_model("mlp"))
    print(app.set_mode("supervised"))
    print("run:", app.run_steps(10))

    payload = app.graph_current()
    assert payload is not None
    assert payload["snapshot"]["model"] == "mlp"

    graph = payload["graph"]
    status = graph["status"]
    assert status in ("ok", "unsupported")

    if status == "ok":
        assert graph["model_type"] == "mlp"
        assert len(graph["inputs"]) == 2
        assert len(graph["hidden"]) >= 1
        assert graph["output"]["id"] == "out"
        assert len(graph["edges"]) >= len(graph["hidden"]) * 3
        assert len(graph["cases"]) == 4
        assert {case["id"] for case in graph["cases"]} == {"00", "01", "10", "11"}

    print("graph status:", status)
    print("ok")


if __name__ == "__main__":
    main()
