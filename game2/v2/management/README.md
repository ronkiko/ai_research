# Management

Management is outside the gameplay data path. It may compose independent
processes, but it must not import their runtime implementations or proxy
gameplay messages.

## Screen infrastructure

```bash
./game2/v2/op/screen_server.sh
./game2/v2/op/screen.sh 1
```

Screen Server owns numbered native Screen windows only. It receives rendered
`ScreenFrame` pixels from Console ScreenSource; it never receives Engine STATE
or Player Vision.

## Training composition

```bash
./game2/v2/op/train.sh --fresh
./game2/v2/op/train.sh --fresh --screen 1
./game2/v2/op/train.sh --resume
```

The Management Training composer launches Console, Trainer, Model, and realtime
Player as separate OS processes using their existing entrypoints. It imports
only shared contracts.

`--screen N` performs an independent Screen Server BIND after a Console starts.
It is not forwarded to any learning/gameplay child process. Screen failure after
startup is spectator-only and does not fail Training.
