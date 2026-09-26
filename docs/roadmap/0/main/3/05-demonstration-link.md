# 0.main.3.05 — Host↔Host DemonstrationLink

Статус: **план**.

Зависимости:
[0.main.3.02](02-escort-consent-and-modes.md),
[0.main.3.04](04-handhold-session.md).

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

## Global TeacherStudentSession slot

Хотя teacher actions идут по scoped Host↔Host demonstration link, GameServer
выдаёт capability и формирует authoritative demonstration telemetry/provenance.
Чтобы один World нельзя было нагрузить сотнями одновременных teachers и
telemetry producers, действует **глобальный singleton teacher↔student slot**.

Рабочая сущность:

```text
TeacherStudentSession
  teaching_session_id
  teacher_entity_id
  student_entity_id
  world_id
  world_epoch
  teacher_controller_generation
  student_controller_generation
  handhold_interaction_id
  purpose
  started_tick
  expires_at
  state = active
```

На одном World одновременно может существовать максимум **одна** такая session.

### Admission

Открытие teacher/demo session выполняется атомарно на GameServer:

1. проверить authentication/capability;
2. проверить teacher identity/session/controller fences;
3. проверить student identity/session/controller fences;
4. проверить active HandholdSession/consent, если они обязательны для режима;
5. проверить global singleton `TeacherStudentSession`;
6. только после этого создать demonstration capability и telemetry producer;
7. вернуть scoped session capability.

Если уже активна другая teacher↔student pair:

```text
TEACHER_SESSION_BUSY
active_teacher_entity_id = ...
active_student_entity_id = ...
retryable = true
```

Отказ происходит **до** создания нового telemetry producer, observer loop,
buffer, P2P capability или dataset writer.

Повтор одного и того же idempotent acquire для той же пары/session может вернуть
существующую session, но не создавать вторую.

Failed/rejected acquire attempts имеют отдельный bounded rate limit, чтобы
admission endpoint сам не стал DoS surface.

### Что именно ограничивается

Singleton относится к паре:

```text
teacher ↔ student
```

а не просто к student.

При first-day handhold:

```text
teacher = Director
student = Yuki
```

Пока эта pair активна, нельзя открыть:

```text
Director  ↔ AI-2
Teacher-2 ↔ AI-2
Teacher-3 ↔ AI-3
```

То есть на одном World одновременно не может быть ни 100 teachers, ни 100
teacher-student demonstration sessions.

### Что не блокируется

Singleton teacher session **не запрещает** другим actors:

- обычный multiplayer;
- LocalActorsObservation;
- RenderFrame/Host state;
- самостоятельное navigation;
- self-learning / reinforcement learning без teacher/demo telemetry;
- обычные server receipts.

Например Yuki может быть student в active teacher session, а AI-2 в это же
время может проходить своё самостоятельное RL-обучение, если его training path
не создаёт teacher/demo telemetry session.

### Один canonical telemetry producer

Active `TeacherStudentSession` имеет один canonical demonstration telemetry
producer с bounded cadence/schema.

Несколько authorized readers не заставляют GameServer повторно собирать те же
teacher/student telemetry frames. Fan-out, если понадобится, строится поверх
одного producer/latest/ring buffer.

Не допускается:

```text
Teacher A ↔ Student A → producer A
Teacher B ↔ Student B → producer B
Teacher C ↔ Student C → producer C
```

на одном World одновременно.

### Lifecycle

TeacherStudentSession освобождается при:

- явном завершении demonstration/teaching session;
- revoke demonstration consent/capability;
- завершении HandholdSession, если link к нему привязан;
- teacher или student disconnect по policy;
- expiry/lease timeout;
- world epoch change;
- replacement controller generation любого участника;
- terminal failure;
- server restart, если runtime session не была безопасно восстановлена.

Освобождение закрывает associated demonstration telemetry/P2P capability и
разрешает открыть следующую teacher↔student pair.

Persisted DemonstrationEpisode не даёт права автоматически восстановить active
TeacherStudentSession после restart.

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

Не входит в `0.main.3.05`:

- behavior cloning;
- imitation loss;
- dataset sampling/training split;
- teacher-quality scoring;
- автоматическое изменение weights;
- VERIFY по demonstration episode.

Будущая learning series сможет использовать этот контракт, не меняя смысл
того, что именно человек продемонстрировал.
