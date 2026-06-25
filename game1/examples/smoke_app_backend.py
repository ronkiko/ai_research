from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application import GameApplication


def main() -> None:
    app = GameApplication.create_default()

    print("games:", [g.key for g in app.list_games()])
    print("models:", [m.key for m in app.list_models()])
    print("modes:", [m.key for m in app.list_modes()])

    print(app.select_game("lie_detector"))
    print(app.select_model("mlp"))
    print(app.set_mode("supervised"))

    state = app.run_steps(10)
    print("run:", state)

    snapshot = app.build_snapshot()
    assert snapshot is not None
    assert "lie_detector" in snapshot.body
    assert "mlp" in snapshot.body

    report = app.render_report_from_body("mlp", snapshot.body, "chip")
    assert "CHIP" in report.body
    assert "Network:" in report.body

    print("snapshot:", snapshot.model, snapshot.game, snapshot.mode, snapshot.accuracy)
    print("report engine:", report.engine)
    print("ok")


if __name__ == "__main__":
    main()
