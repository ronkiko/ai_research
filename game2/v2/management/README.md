# Management

Management is outside the gameplay data path.

## Screen infrastructure

Screen is deliberately split into two independent pieces:

```text
Screen Server  = background broker only
Screen Window  = foreground operator process
```

Start the broker:

```bash
./game2/v2/op/screen_server.sh
```

Open a persistent Screen window in another terminal:

```bash
./game2/v2/op/screen.sh 1
```

The Screen registers slot 1 with the broker and waits. The broker never launches
Pygame and does not own that graphical process.

Training with `--screen 1` only binds a Console ScreenSource to the registered
slot. UNBIND returns the same window to `Waiting for source...`; it does not
close it.

## Training composition

```bash
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --fresh --screen 1
./game2/v2/op/train.sh --resume
```

Management launches Console, Trainer, Model, and Player as independent OS
processes through their public contracts. Screen is a detachable spectator and
never owns Training.


## Bot Profiles and future Bot Profiler

Bot configuration is stored in versioned Bot Profiles under `game2/v2/bots/`.
Training selects one with `--player <bot_id>`:

```bash
./game2/v2/op/train.sh --player player1 --fresh --mode unpaced
```

The future fullscreen **Bot Profiler** GUI must use
`management.bot_profiler_backend.BotProfilerBackend` rather than importing
model or Training internals. Backend details and the UI handoff are documented
in `doc/BOT_PROFILER_BACKEND.md`.

## Bot Profiler

```bash
./game2/v2/op/bot_profiler.sh
```

The native Tk desktop editor opens fullscreen. F11 toggles fullscreen; Esc
returns to a resizable window. Use `--windowed` to start windowed. Python must
include Tk (`python3-tk` on Debian/Ubuntu); no model runtime or PyTorch is loaded
by this utility.

Choose a profile, then select a humanoid callout (or use Up/Down on the diagram)
to inspect its component. The inspector scrolls on smaller desktops. Configuration
choices come from the catalog; topology and metadata come from the profile.
The current Research Strategist is explicitly unavailable. The schematic is an
architectural illustration, not a claim that the current avatar has humanoid
physics.

Edit the display name, initialization seed, precision, catalog configuration or
Motor enabled state, then use **Save profile** / Ctrl+S. The backend validates
the complete profile before publishing it; failed validation keeps the draft
and leaves the stored profile intact. Switching components/profiles or closing
with edits offers Save / Discard / Cancel. **New bot** copies a selected template's
configuration under a new stable ID without copying checkpoints. `catalog` is
reserved for catalog storage.

The editor accepts structurally valid profiles, including disabled Motors and
unspecified seeds. Training separately checks runtime support at launch: the
current runtime requires enabled RIGHT/JUMP Motors and integer seeds. Saving
never modifies existing checkpoints or a running Training session. Use fresh
Training to initialize new weights from changed seeds.

Training state refreshes every five seconds and can be refreshed for another
positive Training Set level. Critic and optimizer appear only in this secondary
state block. All profile and runtime access uses `BotProfilerBackend`.

For isolated experiments, use `--profile-dir PATH` (containing a template
profile) and optionally `--runtime-root PATH`. Graphical verification is explicit
and uses temporary profiles:

```bash
python -m game2.v2.tests.bot_profiler_smoke
# Headless desktop alternative:
xvfb-run -a -s '-screen 0 1600x1000x24' python -m game2.v2.tests.bot_profiler_smoke
```
