# 0.main.3.04 — Server-authoritative HandholdSession

Статус: **план**.

Зависимость: [0.main.3.02](02-escort-consent-and-modes.md).

Цель: сделать «Директор ведёт Юки за руку» реальным взаимодействием двух
GameServer actors, а не только текстом VN.

## Authority

GameServer хранит активную связь:

```text
HandholdSession
  interaction_id
  world_epoch
  teacher_entity_id = Director
  student_entity_id = Yuki
  source_zone
  started_tick
  consent_refs
  state
```

GameTable хранит narrative reference, но не объявляет physical handhold
самостоятельно.

## Precondition

Handhold можно создать только если:

- оба actors существуют;
- одна physical zone;
- допустимая physical distance;
- physical_contact consent active;
- controller/session fences актуальны.

## Physical semantics

Для 1D мира нужен явный assisted physical contract. Он не должен быть teleport.

Конкретная force/constraint модель выбирается в реализации patch после
измерения текущей physics, но обязательны свойства:

- Director остаётся управляемым `Host[human]`;
- Yuki не получает coordinate assignment от клиента;
- GameServer остаётся authority;
- связь имеет bounded separation;
- portal transfer каждого actor остаётся server-authoritative;
- разрыв связи освобождает assisted effect без teleport/reset.

## Provenance

Любое движение Юки при активном handhold маркируется как assisted context:

```text
assisted = true
assistance_kind = handhold
learned_success = false
```

Такой эпизод может быть полезным demonstration evidence, но не VERIFY навыка.

## Lifecycle

Handhold прекращается при:

- отзыве physical consent;
- явном release;
- несовместимой zone;
- превышении допустимого физического separation;
- entity/session/controller replacement;
- world epoch change;
- policy timeout/disconnect по явно заданным правилам.

Прекращение HandholdSession должно отозвать связанную
`DemonstrationCapability`.

## Не путать два соединения

Есть две разные связи:

1. **physical relation** Director↔Yuki — GameServer `HandholdSession`;
2. **network data plane** Host[human]↔Host[yuki] — будущий
   `DemonstrationLink`.

Одна не заменяет другую.
