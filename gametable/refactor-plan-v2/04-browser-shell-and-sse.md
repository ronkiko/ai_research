# Patch 4 — Browser shell / scene renderer / SSE

## Проблема

Текущий browser — один app.js, большой inline SVG, scene CSS частично встроен прямо
в index.html, а состояние обновляется polling каждые 1.8 секунды. Inline style уже
конфликтует с действующим CSP. Это прототип, а не устойчивый shell.

## Цель патча

Сделать frontend тонкой модульной visual-novel оболочкой, полностью управляемой
ViewState и событиями сервера.

## Предлагаемая структура

```text
gametable/web/
  index.html
  js/
    shell.js
    api.js
    events.js
    scene-renderer.js
    dialogue.js
    controls.js
  css/
    shell.css
    scene.css
    dialogue.css
  assets/
    scenes/
    characters/
    props/
```

Структуру можно немного упростить, но index.html должен перестать хранить большие
scene-specific стили и правила.

## Renderer

SceneRenderer принимает scene descriptor и только отображает:

- background;
- pose/expression/slot;
- props;
- transition.

Он не решает, разрешена ли сцена и почему персонаж туда попал.

На первом этапе текущую SVG-графику можно сохранить как asset/component: цель
патча — нормальная граница, а не художественный редизайн.

## SSE

Добавить endpoint `GET /api/events` с server-sent events. Bootstrap остаётся GET.

Минимальные события:

- turn.started
- turn.stage
- scene.transition
- state.changed / turn.completed
- turn.failed

SSE не является authoritative state: событие несёт revision, а клиент при
рассинхронизации может запросить bootstrap заново.

## Composer

Static select заменяется controls, построенными из ViewState.affordances.
Отправка содержит event id, text, intent_id. Browser не изобретает intent.

## CSP

Все собственные style/script остаются external `'self'`. Не добавлять
`'unsafe-inline'`. Проверить отсутствие inline executable JS/style.

## Тесты

- статические файлы проходят syntax checks;
- CSP совместим с реальным index.html;
- SSE endpoint локальный и не выдаёт token cross-origin;
- reconnect/resync не повторяет turn;
- renderer не содержит "lab intent -> laboratory scene";
- controls полностью строятся из affordances;
- отсутствует setInterval polling основного состояния.

## Exit criteria

Browser становится обычным renderer/controller. Он может пережить добавление
новой сцены через server scene descriptor без добавления game-rule if/else.
