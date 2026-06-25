# game1 web migration

## 3-patch plan

- Patch 1: carve out backend application layer in `game1/app/` for module loading, config, runtime session, snapshots, and lab reports without `curses`.
- Patch 2: add a minimal web server on top of that backend facade and wire operator actions through HTTP.
- Patch 3: move remaining lab/report imports out of `ui/`, switch web to primary operator interface, then remove TUI.

## Rules

- Backend code must not import `curses` or other TUI rendering modules.
- Temporary exception until Patch 3: `app/lab.py` may import `ui.lab_engines` as a report-engine bridge.
