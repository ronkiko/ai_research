---
name: "002-игровая_лаборатория_по_изучению_игровых_механик"
description: "Руководство по learning_v1: Motor/Spine обучение, frozen VERIFY и выбор проверенного навыка."
---

# 002 — Лаборатория обучения тела

Активная лаборатория Юки — `learning_v1` из `organism/`. Legacy
`gamelab_v1` не является частью нового персонажного контура.

Основной порядок:

1. `learning_v1_describe` и `learning_v1_skills`;
2. при необходимости `training_prepare` — только с server-issued разрешением
   Директора, если тело ещё не в `training/flat_run`;
3. `motor_train_start(spec_id,budget,request_id)`;
4. frozen one-shot Motor certification через `verify_start`;
5. `spine_train_start(motor_id,spec_id,budget,request_id)`;
6. frozen Spine VERIFY;
7. только после PASS — явный `skill_select`.

Долгие операции асинхронны. Используй `training_status` / `verify_status`;
после сбоя сначала читай status. Повтор того же start request_id не должен
создавать вторую модель/job.

Физические rollout происходят тем же видимым embodiment в
`training/flat_run`. TRAIN/VERIFY держат единый body lease с навигацией.
Setup/reset учебного эпизода — аппаратная подготовка и всегда
`learned_success=false`; не засчитывай её как результат сети.

Motor и Spine — разные артефакты. Сертифицированный Motor immutable. Candidate
Spine не становится production skill от одного факта изменения весов:
нужны frozen VERIFY PASS и `skill_select`.

Public learning API принимает IDs и bounded budget. Он не предоставляет пути к
файлам, Python expressions, прямой motor_x, entity_id или произвольные
координаты. Научный вывод опирается на receipts/metrics/VERIFY, а не на рассказ.
