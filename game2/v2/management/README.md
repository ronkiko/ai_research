# Management

Management is outside the gameplay data path.

The independent Screen Server is started with:

```bash
./game2/v2/op/screen_server.sh
```

A Screen slot is attached to the current Console with:

```bash
./game2/v2/op/screen.sh 1
```

The server owns only numbered native Screen windows. It receives rendered
`ScreenFrame` pixels from a Console `ScreenSource`; it never receives Engine
STATE or Player Vision. A Screen can disappear without affecting the source.

Future Training composition belongs here, but must launch and connect independent
Console, Player, Model, and Trainer processes rather than import their runtime
objects.
