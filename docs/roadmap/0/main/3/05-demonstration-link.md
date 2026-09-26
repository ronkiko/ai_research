# 0.0.3.05 — Host↔Host DemonstrationLink

Статус: **план**.

Зависимости:
[0.0.3.02](02-escort-consent-and-modes.md),
[0.0.3.04](04-handhold-session.md).

Цель: подготовить настоящий teacher channel «Директор учит Юки, ведя её за
руку», **без реализации imitation optimizer**.

## Почему P2P между Hosts

В распределённой topology:

```text
web VPS B                                      GPU/VPS C
Host[human]  ═════ DemonstrationLink ═════►  Host[yuki]
      \                                           /
       └────────── GameServer Gateway ───────────┘
```

Teacher data не обязаны идти через Player Gateway или проксироваться World.

Но GameServer выдаёт и отзывает capability, потому что только он знает
authoritative actors/session/HandholdSession.

## Никаких raw bytes

Запрещено зеркалировать:

- TCP bytes;
- newline JSON wire packets;
- Socket.IO events;
- browser keydown/keyup;
- private Gateway implementation messages.

Юки должна видеть **семантически нормализованный контракт действий игрока**,
одинаковый для human и AI actor.

## ActorDemonstrationEvent v1

Рабочий контракт:

```text
ActorDemonstrationEvent
  schema_version
  demonstration_id
  sequence

  teacher_entity_id
  student_entity_id
  handhold_interaction_id

  world_id
  world_epoch
  zone_id
  controller_generation

  actuator
    kind = motor
    motor_x

  server_result
    action_id
    status
    applied_tick
    world_revision

  observation_before_ref
  observation_after_ref

  provenance
    source = host_normalized_action
    assisted = true
    teacher_demo = true
```

Точный payload before/after должен быть bounded. Предпочтительно ссылаться на
authoritative observation/frame IDs либо передавать минимальный typed delta, а
не копировать весь мир.

## Почему источник — Host[human]

Путь человека:

```text
keyboard
  ↓
Player Gateway coalescing
  ↓
Host[human]
  ↓ normalized motor_x
GameServer
  ↓ receipt
```

Для teacher dataset интересна пара:

```text
normalized actuator request
+
authoritative server outcome
```

а не количество browser key-repeat events.

Так один protocol годится для teacher-human, teacher-AI и будущего recorded
expert.

## DemonstrationCapability

P2P link не открывается просто потому, что два Hosts знают адреса друг друга.

GameServer выдаёт short-lived scoped capability, привязанную минимум к:

```text
teacher_entity_id
student_entity_id
HandholdSession id
world_epoch
teacher controller_generation
allowed event schema
expiry
revocation id
```

Capability только read-only demonstration. Она **не** разрешает
`Host[human]` отправлять actuator commands за Yuki.

## Transport

Series 3 не фиксирует конкретный carrier заранее. Реализация может выбрать
authenticated TLS/QUIC/WebRTC/другой bounded transport после проверки
distributed deployment.

Нормативно:

- end-to-end authenticated peers;
- capability verification;
- bounded message/frame rate;
- monotonic demonstration sequence;
- reconnect без silent replay;
- no arbitrary file/shell access;
- no raw internal service exposure.

## Lifecycle

Link существует только пока разрешает policy:

- active HandholdSession;
- active demonstration consent/capability;
- compatible world epoch;
- matching actor/controller identity.

При revoke/disconnect link закрывается. Physical handhold может при отдельной
policy продолжиться, но новые teacher events не записываются.

## DemonstrationEpisode

Series 3 может сохранять bounded provenance artifact:

```text
DemonstrationEpisode
  episode_id
  teacher
  student
  handhold id
  events[]
  started/ended ticks
  termination_reason
  optimizer_enabled = false
```

Этот artifact **не подаётся автоматически** в Motor/Spine school.

Обязательные labels:

```text
teacher_demo = true
assisted = true
optimizer_enabled = false
learned_success = false
```

## Что намеренно отложено

Не входит в `0.0.3.05`:

- behavior cloning;
- imitation loss;
- dataset sampling/training split;
- teacher-quality scoring;
- автоматическое изменение weights;
- VERIFY по demonstration episode.

Будущая learning series сможет использовать этот контракт, не меняя смысл
того, что именно человек продемонстрировал.
