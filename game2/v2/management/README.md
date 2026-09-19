# Management

Management is the operator and control plane. It is outside the gameplay data
path and must not proxy Vision, Joystick, Engine STATE, model inference, or
Trainer messages.

The first real independent management service is the Screen Server:

```bash
./game2/v2/op/screen_server.sh
```

It runs in the background, publishes
`game2/v2/runtime/screen-server.json`, and owns numbered screen slots. In this
corrective cut those slots are intentionally idle: Console/Training source
binding is deferred to the next block-composition patch.

Management may later compose independent Console, Player, Model, and Trainer
processes through explicit contracts. It must not import their runtime objects.
Closing the Screen Server or any future operator UI must not terminate gameplay
or Training processes.

The autonomous Research Strategist also belongs to this plane. It may request
training, evaluation, exams, candidate comparison, or later Free Play learning,
but it is not a gameplay component and does not control Joystick directly.
