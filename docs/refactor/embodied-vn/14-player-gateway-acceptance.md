# 14 / 14 — Player Gateway cutover, internet hardening и финальная приёмка

Зависимости: [12](12-realtime-boundary.md)–[13](13-player-gateway.md).
Планируемый коммит: `Cut browser realtime traffic over to Player Gateway`.
Цель: A1–A13.

Статус: **реализовано; финальная серия остаётся BLOCKED по evidence**. Этот
патч завершает кодовую часть 01–14 и заменяет 11 как финальный детерминированный
gate. Этап 11 остаётся первым аудитом, на котором был обнаружен непринятый
browser realtime path.

## Production cutover

После 14 обычный web player входит в игру только через `Player Gateway`.

```text
Internet Browser
   ↕ HTTPS / Socket.IO
Player Gateway (Node.js)
   ├─ realtime RenderFrame ↔ Host[human] state
   ├─ human input → Host[human]
   └─ VN/dialogue proxy → GameTable

Host[yuki] ← Motor ← Spine ← Organism/LLM
      \
       └──────────────────────→ GameServer / World ← Host[human]
```

`GameTable` остаётся владельцем character/dialogue/story state и LLM workflow,
но перестаёт быть realtime transport для world frames и browser arrows.
`Player Gateway` может proxy-ить разрешённые GameTable web endpoints, чтобы
browser имел один внешний origin; proxy не меняет VN semantics и не получает
права самостоятельно публиковать реплики или менять story state.

## Что удалить из active browser path

- прямой browser arrow → GameTable → Director Host request/response path;
- synchronous graphics snapshot как условие ответа `/api/state`;
- browser polling, которое само создаёт world observation traffic;
- дублирующий production frame publisher в Python после Node cutover;
- любые координатные команды или portal решения на frontend.

Compatibility/test projection в `graphics/` можно сохранить, если она остаётся
явно неактивной и используется как contract oracle. Не держать два production
источника RenderFrame одновременно.

## Startup и ownership

`gametable/op/start.sh` или новый общий launcher запускает и проверяет:

1. GameServer embodied world;
2. `Host[yuki]`;
3. `Host[human]`;
4. GameTable backend;
5. Player Gateway.

Readiness проверяет каждый слой отдельно. Player Gateway failure не должен
останавливать первые четыре процесса. `--fresh` по-прежнему относится к
GameTable/world first-day state и не удаляет Organism artifacts.

Порты Host/Gateway/World остаются loopback/private. Внешний listener имеет только
Player Gateway. Для production deployment TLS может завершаться на reverse proxy,
но browser никогда не получает internal service addresses.

## Internet hardening

До публичного bind обязательны:

- authenticated player session или эквивалентный server-issued capability;
- strict origin/host policy;
- HTTPS/WSS deployment contract;
- bounded HTTP/Socket.IO body и event sizes;
- per-IP/per-session connection и input rate limits;
- lease cleanup при disconnect и process restart;
- heartbeat/idle timeout;
- bounded queues/latest-only frame delivery;
- no stack traces/internal paths/service ports в public errors;
- structured metrics для connections, dropped/coalesced inputs, Host latency,
  frame age, stale/reconnect и world epoch;
- graceful shutdown с human effort release best-effort.

Rate limiting не является physics anti-cheat. Даже после него GameServer
продолжает валидировать controller fence, world epoch, zone и допустимый input.

## Browser shell

Сохранить текущую игровую композицию: world Canvas + VN dialogue + sidebar.
Меняется transport, а не персонаж и не история.

- world Canvas получает frames через Socket.IO;
- keyboard/mouse/touch human controls идут только в Player Gateway;
- text/dialogue остаётся GameTable workflow через gateway proxy;
- input textbox никогда не посылает arrows в world;
- VN response/LLM latency не блокирует frame stream;
- dialogue может обновляться во время движения;
- reconnect восстанавливает latest authoritative frame и актуальный story view.

## Обязательная ручная realtime-приёмка

На clean/fresh первом дне:

1. P@0 и D@1 подтверждены authoritative frame;
2. через сюжетное согласие включается manual lease;
3. удержание `→` даёт видимое движение Директора без секундной паузы;
4. browser key-repeat не создаёт command flood;
5. Юки следует scripted_escort независимо от Node input loop;
6. оба actors физически касаются portal и переходят по отдельным receipts;
7. dialogue/LLM response во время движения не останавливает physics/frames;
8. focus textbox/blur/disconnect освобождает human effort;
9. restart Player Gateway не двигает/телепортирует actors и не сбрасывает Юки;
10. после reconnect виден fresh epoch/tick/revision, старые frames не replay;
11. shutdown Player Gateway при продолжающем работать GameServer показывает,
    что Yuki/Organism/world не зависят от web frontend.

## Flood/backpressure acceptance

Проверить отдельно:

- 10 000 одинаковых browser input events → bounded число Host commands;
- rapid left/right abuse не превышает configured upstream ceiling;
- медленный browser получает latest frame без растущей очереди;
- две вкладки не становятся двумя simultaneous writers одного actor;
- stale lease/key-up из старой вкладки не отменяет свежий owner;
- malformed/oversized Socket.IO payload не достигает Host;
- Host/Gateway остаются недоступны с внешнего интерфейса.

## Повторная A1–A13 приёмка

Этап 14 заново выполняет deterministic acceptance всей серии и проверяет
документированный источник доказательства для каждого A1–A13. Особое внимание:

- A1–A8: существующие identity/world/learning invariants не регрессировали;
- A9: browser frame — проекция одного authoritative world snapshot;
- A10: active path не возвращает GameLab;
- A11: first-day consent/escort/day-start работают;
- A12: internet player отделён Player Gateway и не получает authority;
- A13: slow/down/flooded web frontend не тормозит Yuki/world.

Stage 11 scientific blockers не исчезают из-за нового frontend. Fresh
Motor/Spine research, frozen VERIFY и live LLM/tool evidence должны быть
выполнены отдельно, если они всё ещё не закрыты. Green Node/Python CI не может
подменить эти доказательства.

## Условие завершения серии

Серия 01–14 считается принятой только когда одновременно выполнены:

- deterministic Python + Node checks;
- реальный browser Player Gateway vertical;
- manual escort/day-start visual acceptance;
- live LLM/MCP checks;
- требуемая scientific TRAIN/VERIFY evidence из 11.

Если хотя бы один блок остаётся BLOCKED, `01-concept.md` не переводится в
принятый статус и никакой timeout/retry workaround не объявляется финальным
решением.


## Реализованный результат Stage 14

- Player Gateway теперь раздаёт действующий GameTable web shell и является
  единственным production browser entrypoint.
- Realtime frames идут только через Socket.IO Player Gateway; старый
  GameTable `/api/frames` удалён из backend surface.
- Browser Director input больше не вызывает `/api/director/*` GameTable.
  Shell использует только `control.acquire/input_state/control.release`
  Player Gateway protocol.
- GameTable стал loopback backend для story/dialogue/state. Разрешённые
  narrative GET/POST маршруты proxy-ятся через Player Gateway; direct
  Director/frames routes proxy запрещает.
- GameTable `/api/state` больше не строит RenderFrame. Независимый
  `WorldObservationPump` читает cached Host[yuki] observation и сохраняет
  ограниченные по display-cell world observations для VN context.
- Production Node projector остаётся parity-checked относительно Python
  `SceneProjector`; Python graphics больше не является вторым production
  frame publisher.
- Player Gateway выдаёт signed HttpOnly SameSite session capability,
  применяет host/origin policy, payload/input/connection bounds и per-IP
  connection ceiling. Public bind требует explicit public mode, TLS contract,
  allowlists и session secret.
- Slow clients получают `volatile` latest-only frames; runtime status содержит
  world epoch/tick, Host freshness, frame age/latency и input coalescing metrics.
- Gateway restart может reclaim старый Host manual lease через fenced transfer;
  active second browser session внутри одного Gateway не может одновременно
  владеть actor.
- `gametable/op/start.sh` запускает Player Gateway как web entrypoint и
  сохраняет GameServer/Hosts как независимые процессы. `--fresh` по-прежнему
  не удаляет Organism artifacts.
- Deterministic acceptance расширен с A1–A11 до A1–A13 и включает Node checks.

## Статус финальной приёмки

Кодовая/контрактная часть Stage 14 может быть PASS только после зелёного CI.
Даже после этого **Overall остаётся BLOCKED**, пока оператор отдельно не
зафиксирует:

1. clean/fresh manual browser escort через Player Gateway;
2. live LLM/MCP vertical;
3. fresh Motor+Spine research + frozen held-out VERIFY;
4. sleep/new-day visual evidence и restart/failure checks.

Эти пункты намеренно не заменяются unit/integration tests.
