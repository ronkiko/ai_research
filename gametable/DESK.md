# Стол Юки · GameTable VN Shell v2

Рабочий стол находится только в сцене laboratory.workstation. Наличие у Директора
intent request_lab_work само по себе не переносит Юки и не открывает инструменты:
сначала должен быть принят CharacterDecision, затем EffectPlanner обязан создать
валидный move/lab_work EffectPlan.

Только ExternalExecutor получает MCP-enabled OpenCode session. Heart, Head,
Narrator и Review остаются tool-less.

## Инструменты

Производственный laboratory_step использует узкий allowlist GameTable для
game_v1 и gamelab_v1. Социальные relationship/Volition/Executive tools не входят
в этот контур.

Перед side effect утверждённый внешний effect записывается в turn journal.
Transport ambiguity означает uncertain; автоматический повтор запрещён.

Асинхронные *_start операции не считаются завершёнными до отдельного наблюдения
status.

## Безопасный live smoke

./gametable/op/check.sh --live использует специальный read-only scope:

- game_v1_health
- game_v1_describe
- gamelab_v1_health
- gamelab_v1_describe

Live smoke не получает training/run/reward setters, player movement или cancel
actions и работает только во временном save.

## Руководства

001-игровой_клиент_и_базовая_информация_об_игре — прямой игровой клиент и
baseline мира.

002-игровая_лаборатория_по_изучению_игровых_механик — основной GameLab workflow,
Motor/Spine, VERIFY/RUN и Host.

003-лаборатория_расширеные_настройки — advanced Host-функции и host_id.

003-лаборатория_плагины_подключаем_и_пишем_свои — зарезервированное руководство
для будущих plugins.

Руководства помогают выполнить конкретную задачу Директора, но не дают модели
право обходить EffectPlan, permissions или наблюдаемый статус инструментов.
