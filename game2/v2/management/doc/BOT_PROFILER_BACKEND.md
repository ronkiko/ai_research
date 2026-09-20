# Bot Profiler Backend

Status: backend contract for the graphical Bot Profiler.

The graphical Bot Profiler is implemented in `management/bot_profiler.py`. This
module boundary lets the fullscreen interface operate without
refactoring Training, importing PyTorch, parsing model classes, or inventing a
second configuration source.

## Source Of Truth

A Bot is configured by a versioned `BotProfile` JSON file under
`game2/v2/bots/`. The default profile is `player1.json`.

`bot_id` identifies the configured Bot for Management and Training. The Bot is
not a special Console entity. During realtime play it attaches through the
normal Player lifecycle and receives an ordinary `PlayerManifest`, Vision and
Joystick capabilities.

The anatomical mapping is:

```text
cerebral_cortex -> Research Strategist / future LLM -> brain
spinal_cord     -> Planner / current CNN             -> spinal cord
motors[]        -> fast reflex controllers           -> body actuators
```

Critic and Adam are Training state. They are not anatomical components and
must not be drawn as part of the Bot body.

## Backend API

The future GUI should depend on
`game2.v2.management.bot_profiler_backend.BotProfilerBackend`.

Available operations:

```text
catalog()
list_bots()
get_profile(bot_id)
create_bot(bot_id, display_name, template_id="player1")
save_profile(profile_dict)
get_component(bot_id, component_ref)
update_component(bot_id, component_ref, changes)
describe_bot(bot_id)
runtime_state(bot_id, training_set_level=1)
```

Component references are stable UI identifiers:

```text
cerebral_cortex
spinal_cord
motor:right
motor:jump
future: motor:<new_motor_id>
```

`describe_bot()` is the preferred left-panel read model. It returns ordered
components with `anatomy_anchor`, role, implementation, configuration,
precision, seed, topology and a human-readable `topology_label` such as
`5-8-3`.

`get_component()` and `update_component()` are intended for the right-side
settings panel. The UI may stage edits locally, but only a valid complete
component/profile may be persisted.

`catalog()` returns the currently advertised component choices and whether
they are runtime-supported. The current Research Strategist/LLM is deliberately
listed as not implemented rather than being faked by the UI.

## Runtime State

`BotRuntimeLayout` is the one path authority shared by Training and the future
Bot Profiler. Default per-Bot state is:

```text
game2/v2/runtime/bots/<bot_id>/level-<N>/
    checkpoints/
        planner.pt
        motor.pt
        critic.pt
        optimizer.pt
        logs/
    episodes/
```

`runtime_state()` reports `fresh`, `partial`, or `ready`, checkpoint
presence and episode count. The UI must not open or deserialize PyTorch
checkpoint files itself.

## GUI Boundary

The future fullscreen GUI may:

- render the Bot silhouette and engineering callouts;
- list/create/select Bot Profiles;
- inspect/edit profile components through this backend;
- show topology, precision, seed and runtime state;
- launch higher-level Management operations through existing Management
  entrypoints when that work is added.

It must not:

- import `torch`, Planner, Motor or Trainer implementation modules;
- edit model weights;
- become a Vision/Joystick bridge;
- receive Engine STATE or hidden map truth;
- invent a second profile format or duplicate topology definitions in widgets;
- special-case a Bot inside Console.

The GUI is an editor and inspector of Management configuration. Gameplay remains
Player -> public peripherals -> Console.

## Current Training Use

Training already accepts:

```bash
./game2/v2/op/train.sh --player player1 --fresh --mode unpaced
./game2/v2/op/train.sh --player player1 --resume --mode unpaced
```

The selected profile determines the current Planner/Motor construction.
Checkpoints, logs and episode datasets are isolated by Bot ID and Training Set
level.
