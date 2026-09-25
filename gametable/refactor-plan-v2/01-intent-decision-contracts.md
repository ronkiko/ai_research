# Patch 1 — Intent / Decision contracts

## Проблема

Сейчас одно поле activity и один список ACTIONS смешивают разные уровни:
"work"/"rest" являются физическими занятиями, "warm"/"playful" — тоном,
"boundary"/"clarify" — позицией в ответе. Такое представление неизбежно приводит
к тому, что UI-команда начинает вести себя как действие мира.

## Цель патча

Ввести отдельные типы входа и результата когнитивной оценки без изменения
browser rendering и без полноценного scene graph. После этого последующие патчи
смогут строиться на чёткой семантике.

## План

1. Заменить внешний смысл `activity` на `intent_id`. На первом cutover можно
   оставить совместимый HTTP-field только внутри адаптера, но authoritative
   runtime packet должен уже работать с intent.
2. В rules определить минимальный каталог Director intents:
   - talk
   - request_lab_work
   - request_rest
   - request_sleep
   - leave_or_move_request (если нужен уже в этом шаге)
3. Разделить результат выбора персонажа:
   - disposition: respond / accept / decline / clarify
   - tone: neutral / warm / playful / firm / shy / upset (минимальный набор,
     без попытки моделировать все эмоции)
4. Heart/Head scores должны оценивать disposition, а tone вычисляется отдельно
   из состояния/оценок либо из отдельного bounded contract.
5. Убрать `work`, `rest`, `warm`, `playful` из одного общего decision enum.
6. Обновить fixed anchors так, чтобы anchor фиксировал disposition, а не
   физический эффект.
7. Сохранить детерминированный выбор и влияние обеих независимых сторон.

## Почему tone не должен быть decision

"Мне хочется ответить тепло" не означает "я согласилась выполнить просьбу".
Точно так же отказ может быть мягким, смущённым или твёрдым. Разделение убирает
скрытое логическое правило, где стиль речи случайно становится решением.

## Тесты

- одинаковый frozen event идёт Heart и Head;
- изменение только Heart или только Head способно изменить disposition;
- tone не меняет disposition;
- request_lab_work + decline не кодирует никакого work-effect;
- chat/talk не может породить лабораторный side effect;
- stale/spoofed report по-прежнему отвергается;
- deterministic replay даёт тот же CharacterDecision.

## Exit criteria

После этого коммита runtime уже имеет явный `DirectorIntent -> CharacterDecision`,
но мир ещё может использовать старую scene реализацию до Patch 2. Не добавлять
SSE и не реорганизовывать frontend в этом патче.
