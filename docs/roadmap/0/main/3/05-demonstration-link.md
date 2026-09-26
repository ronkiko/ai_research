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

## TeacherStudentSession registry

GameServer выдаёт capability и формирует authoritative demonstration
telemetry/provenance. Ограничение здесь **не глобальное на весь World**.

Нормативная cardinality:

```text
student_entity_id → 0 or 1 active teacher
```

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

GameServer хранит registry активных teaching sessions с уникальностью по
`student_entity_id`.

### Admission

Открытие teacher/demo session выполняется атомарно:

1. проверить authentication/capability;
2. проверить teacher identity/session/controller fences;
3. проверить student identity/session/controller fences;
4. проверить HandholdSession/consent, если они обязательны для режима;
5. проверить, что у данного `student_entity_id` нет другого active teacher;
6. только после этого создать demonstration capability и telemetry producer;
7. зарегистрировать session и вернуть scoped capability.

Если student уже занят другим teacher:

```text
STUDENT_ALREADY_HAS_TEACHER
student_entity_id = ...
active_teacher_entity_id = ...
retryable = true
```

Отказ происходит до создания второго telemetry producer, observer loop,
buffer, P2P capability или dataset writer для этого student.

Повтор idempotent acquire для той же пары/session может вернуть уже существующую
session, но не создавать дубль.

Rejected acquire attempts имеют bounded rate limit, чтобы сам admission endpoint
не был DoS surface.

### Допустимая параллельность

Разные students могут обучаться одновременно:

```text
Teacher A ↔ Student 1
Teacher B ↔ Student 2
Teacher C ↔ Student 3
```

Также один teacher может иметь несколько students, если отдельная policy этого
не запрещает:

```text
Teacher A ↔ Student 1
Teacher A ↔ Student 2
```

Series 3 не вводит `max_students_per_teacher`.

Главный инвариант только один:

```text
max_active_teachers_per_student = 1
```

### Один canonical producer на student

Для каждого active student существует максимум один canonical demonstration
telemetry producer, соответствующий его единственной active teacher relation.

Несколько authorized readers не должны заставлять GameServer повторно собирать
одинаковую telemetry для того же student. Fan-out строится поверх одного
producer/latest/ring buffer.

Запрещено:

```text
Teacher A → Student 1 → producer A
Teacher B → Student 1 → producer B
```

Разрешено:

```text
Teacher A → Student 1 → producer 1
Teacher B → Student 2 → producer 2
```

Общая нагрузка многих одновременно обучаемых students должна отдельно
ограничиваться server capacity/rate/resource policy. Это отдельная защита и не
меняет relation cardinality.

### Lifecycle

TeacherStudentSession для конкретного student освобождается при:

- явном завершении demonstration/teaching session;
- revoke demonstration consent/capability;
- завершении связанного HandholdSession;
- teacher или student disconnect по policy;
- expiry/lease timeout;
- world epoch change;
- replacement controller generation любого участника;
- terminal failure;
- server restart, если runtime session не была безопасно восстановлена.

После освобождения student может принять нового teacher.

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
