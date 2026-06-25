# game1 web migration

## 3-patch plan

- Patch 1: done. Backend application layer lives in `game1/app/` for module loading, config, runtime session, snapshots, and lab reports without `curses`.
- Patch 2: done. Minimal web server MVP now wraps that backend facade and exposes operator actions through HTTP plus a plain HTML/CSS/JS UI.
- Patch 3: move remaining lab/report imports out of `ui/`, switch web to primary operator interface, then remove TUI.

## Rules

- Backend code must not import `curses` or other TUI rendering modules.
- Temporary exception until Patch 3: `app/lab.py` may import `ui.lab_engines` as a report-engine bridge.

## Patch 2 result

- Default launch path is now web-first:

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
  - current snapshot report rendering and saved snapshot report rendering.

## Temporary legacy path

- `python3 engine.py --tui` still launches the old curses interface during the transition.
- TUI removal, report-engine import cleanup, and final dead-code cleanup stay in Patch 3.
