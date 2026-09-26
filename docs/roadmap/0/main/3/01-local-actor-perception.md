# 0.main.3.01 — Local actor perception

Статус: **план**.

Цель: дать Юки честное восприятие игровых и неигровых персонажей, находящихся
в той же физической локации.

## Почему это отдельный patch

GameServer уже знает всех entities, но наличие entity в authoritative snapshot
ещё не означает, что LLM персонажа должна получать полный world state.

Нужен явный sensor contract между GameServer/Host и Organism/Character context.

Пример:

```text
Yuki:
  zone = hallway

local_actors:
  - Director
    relative_x = +4.2
    relative_vx = 0.0
```

Тогда вопрос Директора «где мы сейчас?» имеет фактический social context:
Директор находится рядом, поэтому Narrator/LLM может естественно сказать
«мы в коридоре», а не говорить о себе как об изолированном объекте.

Формулировку реплики по-прежнему выбирает персонаж; sensor не генерирует текст.

## Контракт

Рабочее имя:

```text
LocalActorsObservation v1
```

Минимальные поля:

```text
schema_version
world_id
world_epoch
tick
world_revision
observer_entity_id
zone_id

actors[]
  entity_id
  actor_kind        # player | npc | character
  relative_x
  relative_vx
  presence_state
  known_identity?   # только если identity доступна персонажу
```

## Ограничения

- только текущая physical zone Юки;
- никакой информации об actors в других zones;
- никакого чтения их скрытых controller state, intents, memory или relationship;
- никаких будущих координат;
- никакого «магического» знания portal/world bounds сверх доступного sensor
  contract;
- sensor read-only.

В первой 1D версии допустимо видеть всех actors той же zone. Если позже появятся
vision/occlusion/range mechanics, contract расширяется явной perception policy,
а не скрытым фильтром.

## Identity и presence

Физическое присутствие и знание личности различаются.

Юки может видеть:

```text
unknown_actor at dx=+20
```

не зная имени.

Для уже знакомого Director identity resolver может дать:

```text
known_identity = character.director
```

Это narrative knowledge, а не физический authority.

## Потребители

- Organism sensor/context;
- GameTable working context для персонажа;
- будущий independent follow;
- будущий handhold precondition.

Player Gateway не является sensor authority для Юки.

## Приёмка

- Director и Yuki в одной zone → Director присутствует в observation Юки;
- Director переходит в другую zone → исчезает;
- NPC той же zone присутствует как actor;
- другой-zone NPC не виден;
- relative position выводится из одного authoritative world frame;
- sensor не содержит чужие actuator commands или private Host data.
