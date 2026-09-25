# Стол Юки · embodied GameTable

`workstation` теперь semantic object внутри physical location `laboratory`.
Старое имя VN-сцены `laboratory.workstation` остаётся только compatibility
данными до cutover 09.

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

Legacy game_v1/gamelab_v1 остаются подключёнными до этапов 08–09 только ради
совместимости и safe live smoke. Новые physical actions GameTable направляет в
канонический world/navigation слой, а не через direct game_v1_move.

Learning permission не выводится из navigation permission. Обучение, verify,
skill selection и workstation session вводятся отдельно в патче 08.

## Recovery

GameTable хранит approved proposal в action_outbox до dispatch. Action/job status
и world observations имеют отдельный commit/inbox. После ambiguous crash side
effect не повторяется автоматически. Диалоговый publish может не состояться,
но уже наблюдённое движение остаётся фактом мира.

`./gametable/op/check.sh --live` пока остаётся недеструктивным: normal chat +
pure action contract + read-only legacy health/describe.
