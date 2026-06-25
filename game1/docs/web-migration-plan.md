# game1 web migration

## 3-patch plan

- Patch 1: done. Backend application layer lives in `game1/app/` for module loading, config, runtime session, snapshots, and lab reports without `curses`.
- Patch 2: done. Minimal web server MVP now wraps that backend facade and exposes operator actions through HTTP plus a plain HTML/CSS/JS UI.
- Patch 3: done. Remaining lab/report code moved out of `ui/`, old TUI removed, `engine.py` is web-only, and graph placeholder seam added.

## Rules

- Backend and web code must not import `curses` or legacy `ui` modules.
- `lab/engines/` is the canonical home for report engines.

## Final state

- Default launch path is web-only:

```bash
python3 engine.py
python3 engine.py --no-open
python3 engine.py --port 0
```

- Port policy:
  - default host is `127.0.0.1`;
  - default bind tries `8765..8799`;
  - `--port 0` asks the OS for a free port;
  - `--port N` uses that exact port or exits with a clear error.
- Browser auto-open uses `webbrowser.open(url)` unless `--no-open` is passed.
- Web UI covers:
  - game/model/mode selection;
  - start/stop/tick/step run controls;
  - snapshot save/list/read;
  - lab engine list;
  - current snapshot report rendering and saved snapshot report rendering;
  - graph placeholder panel with `Open current graph` / `Open snapshot graph`.

## Patch 3 result

- `engine.py` launches only `web.server.main`.
- Legacy terminal flag is removed.
- Old terminal launcher and the entire legacy UI tree are removed.
- Report engines moved into `lab/engines/`.
- `app/lab.py` now imports `from lab.engines.registry import ENGINES`.
- Added API placeholder seam:
  - `GET /api/graph?snapshot=<id>`;
  - `POST /api/graph-current`.
- Web is the only operator interface for `game1`.
