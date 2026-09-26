# 0.main.3.02 — Escort consent and modes

Статус: **план**.

Зависимость: [0.main.3.01](01-local-actor-perception.md).

Цель: заменить одно неявное «сопровождение» явным выбором персонажа.

## EscortOffer

Story создаёт typed offer, например:

```text
EscortOffer
  offer_id
  director_entity_id
  yuki_entity_id
  source_zone
  target_zone
  available_modes:
    HAND_IN_HAND
    FOLLOW_INDEPENDENTLY
    DECLINE
```

Решение принимает Юки как персонаж, а не physics layer.

## Три исхода

### HAND_IN_HAND

Юки согласна на физический контакт и assisted escort.

### FOLLOW_INDEPENDENTLY

Юки согласна идти, но не брать Директора за руку.

### DECLINE

Юки не принимает сопровождение.

Ни Story, ни GameServer не должны подменять один исход другим ради прохождения
tutorial.

## Consent scopes

Минимально различать:

```text
follow_consent
physical_contact_consent
demonstration_consent
```

Они имеют разные системные последствия.

`HAND_IN_HAND` может в первой реализации выдавать physical + demonstration
consent одним осознанным story choice, но внутри contracts это **две разные
capabilities**, чтобы одну можно было отозвать без переписывания всей механики.

Примеры:

```text
follow=yes
physical_contact=no
demonstration=no
→ FOLLOW_INDEPENDENTLY
```

```text
follow=yes
physical_contact=yes
demonstration=yes
→ HAND_IN_HAND + teacher channel
```

## Relationship/trust

Характер и история отношений входят в LLM/decision context и могут влиять на
выбор Юки. Но patch не вводит формулу:

```text
trust >= 42 → automatically accept hand
```

Особенно в первый день нормальным результатом является отказ от физического
контакта при согласии идти самостоятельно.

Это остаётся решением персонажа с явным persisted result.

## Revocation

Physical/demo consent можно отозвать. Отзыв должен:

- закрыть соответствующую capability;
- прекратить handhold или demo channel;
- не телепортировать actors;
- не стирать уже записанную provenance историю;
- не отменять автоматически сам `follow_consent`, если персонаж хочет
  продолжить идти самостоятельно.

## Не является обучением

Выбор режима не меняет Motor/Spine weights и не является reward.

Он только определяет, какие physical/social/data capabilities доступны дальше.
