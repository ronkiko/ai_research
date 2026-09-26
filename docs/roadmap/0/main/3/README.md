# 0.main.3 — Social embodiment, escort and demonstration

Статус: **roadmap-заготовка, runtime не реализован**.

Release: `0`  
Branch: `main`  
Series: `3`

Series 3 развивает уже существующий честный multiplayer world:

- человек играет за **Director** через `Host[human]`;
- AI-организм играет за **Yuki** через `Host[yuki]`;
- GameServer одинаково фиксирует обоих как физических actors;
- Player Gateway и Organism могут находиться на разных машинах;
- World/Physics остаются единственным authority.

Главная тема серии — превратить сопровождение из временного сюжетного
исключения в явную механику взаимодействия двух игроков.

## Базовая модель

Юки должна различать:

1. **кто находится рядом с ней в текущей локации**;
2. **идёт ли она за Директором самостоятельно**;
3. **согласилась ли она на физический контакт «вести за руку»**;
4. **разрешён ли при этом teacher/demonstration channel между Hosts**.

Это четыре разных контракта и они не должны смешиваться.

## Две ветки сопровождения

```text
EscortOffer
    │
    ├── HAND_IN_HAND
    │      │
    │      ├─ physical-contact consent
    │      ├─ server-authoritative HandholdSession
    │      └─ scoped DemonstrationLink
    │             Host[human] ═══► Host[yuki]
    │
    ├── FOLLOW_INDEPENDENTLY
    │      │
    │      ├─ no handhold
    │      ├─ no Host↔Host teacher link
    │      └─ Yuki acts from her own sensors/controller
    │
    └── DECLINE
           └─ no escort
```

### HAND_IN_HAND

Юки добровольно принимает физическое сопровождение. Это не hidden bonus к
характеру и не teleport.

GameServer хранит реальное взаимодействие между двумя actors, а сервером
выданная capability может разрешить отдельный read-only demonstration data
plane:

```text
Host[human]  ═════ authenticated P2P demonstration ═════► Host[yuki]
```

Юки видит **нормализованные контрактные действия учителя**, а не raw TCP bytes,
browser key events или внутренний GameServer packet stream.

Такой эпизод:

```text
assisted = true
teacher_demo = true
learned_success = false
```

Он может стать материалом для будущего imitation learning, но **Series 3 не
обучает модель на этих данных**.

### FOLLOW_INDEPENDENTLY

Юки отказывается от руки, но соглашается идти.

Нет:

- физического handhold;
- P2P teacher channel;
- demonstration dataset от действий Директора.

Юки использует только свои обычные sensors и controller. Текущий
`ScriptedEscortController` рассматривается как временная детерминированная
реализация именно этой ветки, пока самостоятельная learned navigation ещё не
готова для сюжетного сценария.

Такой эпизод не является teacher demonstration.

## Отношения и обучение

Relationship/trust **не добавляется в physics reward**.

Запрещённая причинность:

```text
love_score → PPO bonus → Yuki magically moves better
```

Целевая причинность:

```text
relationship / trust
        ↓
character decision + consent
        ↓
physical cooperation becomes available
        ↓
teacher demonstration data becomes available
        ↓
future learning may use better information
```

То есть хорошие отношения могут сделать Директора реально полезным учителем,
не нарушая научную честность Motor/Spine.

Отказ Юки от руки не блокирует задачу: она может идти сама и в будущем учиться
из собственных rollout.

## Distributed architecture

Series 3 опирается на
[distributed runtime contract](../../../../deployment/distributed-runtime.md).

Например:

```text
GameServer VPS A

Host[human] / web VPS B  ═══ P2P demo ═══► Host[yuki] / GPU VPS C
          \                                 /
           └──────── GameServer Gateway ───┘
```

Host↔Host P2P — **узкое исключение только для authorized demonstration data**.
Оно не заменяет GameServer Gateway и не переносит physical authority на clients.

## Не более одного teacher на одного student

Teacher/demo telemetry — отдельный server-side resource, но Series 3 **не**
вводит глобальный singleton на весь World.

Нормативный инвариант:

```text
for each student_entity_id:
    active_teacher_count ∈ {0, 1}
```

То есть одновременно допустимо:

```text
Teacher A ↔ Student 1
Teacher B ↔ Student 2
Teacher A ↔ Student 3
Student 4 ↔ none
```

Но запрещено:

```text
Teacher A ─┐
           ├─→ Student 1
Teacher B ─┘
```

У одного student не может быть двух активных teachers одновременно.

GameServer должен проверять уникальность active teaching relation по
`student_entity_id` **до** создания demonstration capability, telemetry
producer, subscription, buffer или dataset stream.

Если student уже имеет активного teacher, второй acquire получает typed
`STUDENT_ALREADY_HAS_TEACHER` / эквивалентный отказ без создания второго
telemetry producer для этого student.

Несколько Hosts/connections/sessions не могут обойти это ограничение.

Это правило не ограничивает число студентов в World и не запрещает параллельное
teacher-assisted обучение разных students. Общие server capacity/rate limits
для большого числа одновременных students являются отдельной resource-policy
задачей и не должны подменяться этим cardinality invariant.

Подробный admission/lifecycle contract:
[0.main.3.05](05-demonstration-link.md).

## Не реализуем в Series 3

- imitation optimizer;
- supervised/behavior-cloning training;
- automatic merge demonstration dataset в Motor/Spine;
- reward bonus от relationship;
- remote control Yuki через `Host[human]`;
- raw packet mirroring;
- direct Browser→Host[yuki] или Player Gateway→Host[yuki];
- доказательство learned skill по assisted handhold episode.

## Roadmap patches

| Version | Patch | Result |
| --- | --- | --- |
| `0.main.3.01` | [Local actor perception](01-local-actor-perception.md) | Юки получает честное same-zone присутствие других actors |
| `0.main.3.02` | [Escort consent and modes](02-escort-consent-and-modes.md) | HAND_IN_HAND / FOLLOW_INDEPENDENTLY / DECLINE и отдельные consent scopes |
| `0.main.3.03` | [Independent follow](03-independent-follow.md) | Текущий scripted follow получает правильный контракт без teacher data |
| `0.main.3.04` | [Handhold session](04-handhold-session.md) | GameServer-authoritative физическое взаимодействие и assisted provenance |
| `0.main.3.05` | [Demonstration link](05-demonstration-link.md) | Scoped Host↔Host P2P protocol и записываемое demo evidence без обучения |

Порядок важен: demonstration link строится поверх perception, consent и
server-authoritative physical relation, а не наоборот.
