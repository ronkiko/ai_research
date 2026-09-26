# Стол Юки · embodied GameTable

`workstation` — semantic object внутри authoritative physical location
`laboratory`. Старое имя VN-сцены `laboratory.workstation` после cutover 09
сохраняется только как migrated historical field; physical location из него
не выводится.

Просьба «поработай в лаборатории» разбивается по фактам:

1. DirectorIntent;
2. CharacterDecision accept/decline/clarify;
3. если принято — navigation proposal к `laboratory`;
4. после observed arrival — отдельный `approach(workstation)`;
5. только подтверждённое workstation interaction может открыть learning/work scope
   этапа 08.

Принятое решение не означает ни прибытие, ни посадку, ни обучение.

## Tools

Обычные Heart/Head/Narrator/Review — deny-all.

Navigation существует вне лаборатории. Код знает отдельный
`NAVIGATION_TOOLS` allowlist, но semantic target всё равно ограничивается
server-side proposal scope; модель не может выбрать другое тело или координату.

`game_v1/gamelab_v1` больше не подключены к активному GameTable OpenCode.
Physical actions идут через `navigation_v1`, обучение — через отдельный
`learning_v1`. Navigation permission не выводит learning permission и наоборот.

## Recovery

GameTable хранит approved proposal в action_outbox до dispatch. Action/job status
и world observations имеют отдельный commit/inbox. После ambiguous crash side
effect не повторяется автоматически. Диалоговый publish может не состояться,
но уже наблюдённое движение остаётся фактом мира.

`./gametable/op/check.sh --live` остаётся недеструктивным: normal chat +
pure action contract + read-only `learning_v1_describe/skills`.

## learning_v1 и курс

Физическая практика проходит в `training/flat_run`, а не «в ноутбуке».
Если тело ещё не на курсе, `training_prepare` требует заранее выданное
server-side разрешение Директора и выполняет только assisted setup. Оно не
считается learned navigation.

Training/VERIFY захватывают общий body lease. Пока job владеет телом,
обычная navigation должна видеть busy. Read-only `describe/skills/status`
не требуют сидеть за workstation. Candidate Spine не становится production
skill до отдельного VERIFY PASS и `skill_select`.
