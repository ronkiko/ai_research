# Patch 3 — Server-driven ViewState / affordances

## Проблема

Текущий app.js знает слишком много: сам переводит raw state в названия сцен,
вычисляет display relation labels и предлагает статический select действий.
Это превращает browser в второй игровой движок.

## Цель патча

Добавить ViewProjector на сервере. Browser получает готовое представление текущей
visual-novel сцены и список допустимых интентов, не реконструируя правила мира.

## ViewState

Минимальный контракт:

```json
{
  "revision": 12,
  "scene": {
    "id": "laboratory.workstation",
    "background": "laboratory_day",
    "transition": null,
    "character": {
      "pose": "seated_working",
      "expression": "focused",
      "slot": "center"
    },
    "props": ["desk", "laptop"]
  },
  "stats": {},
  "relationship_label": "...",
  "affordances": [],
  "busy": false,
  "stage": null
}
```

Это presentation contract. Browser может форматировать цифры, но не решать
доступность world actions.

## Affordances

Примеры:

hallway:
- talk
- request_lab_work
- request_rest

laboratory.workstation:
- talk
- request_lab_work
- leave_lab
- request_rest

Affordance описывает intent_id, label и UI kind. Он не обещает, что Юки примет
просьбу: это доступное действие Директора, не принудительный результат.

## API

`/api/state` временно можно сохранить как bootstrap endpoint, но его payload
должен стать ViewState-oriented. Raw authoritative state и internal calculations
не нужны обычному frontend.

Audit endpoint остаётся операторским и отдельно показывает доказательства.

## Browser simplification

После этого app.js:

- не содержит locations mapping как правило мира;
- не содержит hard-coded availability лаборатории;
- не вычисляет relationship stages из скрытых порогов, если label уже спроецирован;
- рендерит список affordances от сервера.

## Тесты

- hallway ViewState содержит ожидаемые affordances;
- laboratory ViewState содержит leave_lab;
- unavailable intent отсутствует, а forged intent отвергается сервером;
- ViewProjector не мутирует GameState;
- presentation labels не влияют на reducer;
- public bootstrap не выдаёт unreviewed drafts/internal prompts.

## Exit criteria

К концу патча сервер полностью определяет "что сейчас на экране" и "что Директор
может запросить". Browser ещё может использовать монолитный JS и polling — это Patch 4.
