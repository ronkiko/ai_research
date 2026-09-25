# Patch 6 — Cutover / hardening / permanent docs / cleanup

## Цель

Удалить временные совместимости и доказать весь VN Shell v2 вертикально. Это не
патч новых возможностей: он завершает миграцию, закрывает дырки и делает новую
архитектуру единственной.

## Legacy removal

Удалить либо перестать использовать:

- старое request поле activity как authoritative contract;
- единый смешанный ACTIONS contract;
- raw location mutation из Director request;
- browser locations/rules mappings;
- static activity select;
- polling state timer;
- inline scene style;
- prompt-фразы, вручную утверждающие текущую локацию;
- временные compatibility branches, добавленные в Patch 1–5.

Не удалять старые game1/game2 и не проводить unrelated cleanup.

## Permanent documentation

Обновить как минимум:

- `gametable/README.md`
- `gametable/ROLEPLAY_ENGINE_DESIGN.md`
- `gametable/AGENTS.md`
- `gametable/DESK.md` при изменении laboratory interaction contract

Документация должна описывать реальную v2 implementation, а не будущий план.

## Unit regression suite

Обязательно покрыть:

- fresh hallway;
- accepted move into laboratory;
- declined/clarify request remains in place;
- normal return transition;
- invalid/forged intent rejection;
- deterministic reducer;
- Heart/Head causal independence;
- tone independent from decision;
- ViewState/affordances;
- SSE revision/reconnect semantics;
- CSP without unsafe-inline;
- idempotent event replay;
- crash recovery around external effects;
- MCP allowlist and zero tools outside laboratory execution;
- draft non-leak/fallback.

## Live checks

`./gametable/op/check.sh --live` должен по-прежнему использовать временный save
и удалять owned OpenCode sessions.

Нужны две живые вертикали:

1. normal chat: Heart + Head -> decision -> narration -> review -> commit;
2. safe laboratory route: перейти в лабораторную сцену и доказать MCP boundary
   безопасными `health`/`describe` calls либо эквивалентным read-only сценарием.

Live test не должен обучать Motor/Spine, двигать player или загрязнять save Директора.

## CI

Workflow paths должны включать исходники MCP surface, на которые реально зависит
GameTable, включая `gameclient/v1/clients/mcp.py` и релевантные GameLab contracts.
Не требовать live paid model в обычном CI.

## Финальная проверка критериев

Сверить реализацию с `00-goal-and-acceptance.md` пункт за пунктом. Если какой-то
критерий не доказан, временные планы не удалять и патч не считать завершённым.

Только после PASS:

1. перенести необходимые устойчивые объяснения в permanent docs;
2. удалить весь `gametable/refactor-plan-v2/`;
3. выполнить финальные static/unit checks;
4. сделать этот шестой implementation commit fast-forward.

Таким образом история будет состоять из одного подготовительного plan commit и
шести последовательных implementation commits, а HEAD после Patch 6 не будет
содержать временные проектные записки.
