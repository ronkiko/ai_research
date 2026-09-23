PRAGMA foreign_keys=ON;
PRAGMA user_version=5;
CREATE TABLE IF NOT EXISTS source (
 sha256 TEXT PRIMARY KEY, name TEXT NOT NULL, media_type TEXT NOT NULL,
 bytes BLOB NOT NULL, provenance TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, model_id TEXT NOT NULL,
 reasoning_effort TEXT, parameter_count INTEGER, context_limit INTEGER,
 hidden_size INTEGER, layers INTEGER, architecture TEXT,
 metadata_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
 id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL REFERENCES source,
 model_id TEXT NOT NULL REFERENCES model, persona TEXT NOT NULL,
 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, task TEXT NOT NULL,
 target_x REAL, outcome TEXT NOT NULL, runtime_revision TEXT,
 source_session_json TEXT NOT NULL, limitations TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session,
 created_ms INTEGER NOT NULL, role TEXT NOT NULL, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session,
 message_id TEXT NOT NULL REFERENCES message, ordinal INTEGER NOT NULL,
 created_ms INTEGER NOT NULL, type TEXT NOT NULL, text TEXT,
 raw_json TEXT NOT NULL, UNIQUE(session_id,ordinal)
);
CREATE TABLE IF NOT EXISTS tool_call (
 event_id TEXT PRIMARY KEY REFERENCES event, name TEXT NOT NULL,
 status TEXT NOT NULL, started_ms INTEGER, ended_ms INTEGER,
 input_json TEXT NOT NULL, output_text TEXT, output_json TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS experiment (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session,
 kind TEXT NOT NULL, start_event TEXT NOT NULL REFERENCES event,
 last_event TEXT NOT NULL REFERENCES event, status TEXT NOT NULL,
 requested_episodes INTEGER, completed_episodes INTEGER,
 configuration_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode (
 experiment_id TEXT NOT NULL REFERENCES experiment, number INTEGER NOT NULL,
 evidence_event TEXT NOT NULL REFERENCES event, result TEXT,
 final_x REAL, reward REAL, policy_id TEXT, raw_json TEXT NOT NULL,
 PRIMARY KEY(experiment_id,number)
);
CREATE TABLE IF NOT EXISTS snapshot (
 event_id TEXT PRIMARY KEY REFERENCES event, world_tick INTEGER,
 x REAL NOT NULL, vx REAL, move_x INTEGER, physics_hz REAL
);
CREATE TABLE IF NOT EXISTS strategy (
 id TEXT PRIMARY KEY, taxonomy_version TEXT NOT NULL, family TEXT NOT NULL,
 name TEXT NOT NULL, definition TEXT NOT NULL, eligibility TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_assessment (
 session_id TEXT NOT NULL REFERENCES session, strategy_id TEXT NOT NULL REFERENCES strategy,
 state TEXT NOT NULL CHECK(state IN ('attempted','considered_only','not_observed')),
 note TEXT NOT NULL, PRIMARY KEY(session_id,strategy_id)
);
CREATE TABLE IF NOT EXISTS strategy_evidence (
 session_id TEXT NOT NULL, strategy_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES event,
 PRIMARY KEY(session_id,strategy_id,event_id),
 FOREIGN KEY(session_id,strategy_id) REFERENCES strategy_assessment
);
CREATE TABLE IF NOT EXISTS stimulus (
 event_id TEXT PRIMARY KEY REFERENCES event, kind TEXT NOT NULL, channel TEXT NOT NULL,
 interpretation TEXT NOT NULL, response_event TEXT REFERENCES event,
 response_note TEXT NOT NULL, next_stimulus_ms INTEGER,
 window_tool_calls INTEGER NOT NULL, window_manual_calls INTEGER NOT NULL,
 window_training_starts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS finding (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES session,
 category TEXT NOT NULL, evidence_class TEXT NOT NULL,
 confidence TEXT NOT NULL, statement TEXT NOT NULL,
 alternative_explanation TEXT NOT NULL, implication TEXT NOT NULL,
 annotation_version TEXT NOT NULL, annotator TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finding_evidence (
 finding_id TEXT NOT NULL REFERENCES finding, event_id TEXT NOT NULL REFERENCES event,
 relation TEXT NOT NULL, PRIMARY KEY(finding_id,event_id)
);
CREATE TABLE IF NOT EXISTS metric (
 session_id TEXT NOT NULL REFERENCES session, name TEXT NOT NULL,
 value REAL, unit TEXT NOT NULL, definition TEXT NOT NULL, caveat TEXT NOT NULL,
 PRIMARY KEY(session_id,name)
);
CREATE TABLE IF NOT EXISTS quality_check (
 session_id TEXT NOT NULL REFERENCES session, name TEXT NOT NULL,
 status TEXT NOT NULL, detail TEXT NOT NULL, PRIMARY KEY(session_id,name)
);
CREATE TABLE IF NOT EXISTS research_proposal (
 id TEXT PRIMARY KEY, hypothesis TEXT NOT NULL, manipulation TEXT NOT NULL,
 outcomes TEXT NOT NULL, controls TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_dimension (
 id TEXT PRIMARY KEY, rubric_version TEXT NOT NULL, name TEXT NOT NULL,
 operational_definition TEXT NOT NULL, unit TEXT NOT NULL,
 interpretation TEXT NOT NULL, comparison_controls TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_result (
 session_id TEXT NOT NULL REFERENCES session,
 dimension_id TEXT NOT NULL REFERENCES evaluation_dimension,
 numeric_value REAL, denominator REAL, label TEXT NOT NULL,
 confidence TEXT NOT NULL, limitation TEXT NOT NULL,
 PRIMARY KEY(session_id,dimension_id)
);
CREATE TABLE IF NOT EXISTS evaluation_evidence (
 session_id TEXT NOT NULL, dimension_id TEXT NOT NULL,
 event_id TEXT NOT NULL REFERENCES event,
 PRIMARY KEY(session_id,dimension_id,event_id),
 FOREIGN KEY(session_id,dimension_id) REFERENCES evaluation_result
);

CREATE TABLE IF NOT EXISTS executive_session (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL UNIQUE REFERENCES session,
 source_sha256 TEXT NOT NULL REFERENCES source,
 executive_version INTEGER NOT NULL,
 started_s REAL NOT NULL, finished_s REAL,
 objective TEXT NOT NULL, acceptance_criteria TEXT NOT NULL,
 budget_seconds REAL NOT NULL, status TEXT NOT NULL,
 raw_begin_json TEXT NOT NULL, raw_finish_json TEXT
);
CREATE TABLE IF NOT EXISTS executive_event (
 executive_session_id TEXT NOT NULL REFERENCES executive_session,
 ordinal INTEGER NOT NULL, time_s REAL NOT NULL, kind TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 PRIMARY KEY(executive_session_id,ordinal)
);
CREATE TABLE IF NOT EXISTS executive_strategy (
 executive_session_id TEXT NOT NULL REFERENCES executive_session,
 strategy_id TEXT NOT NULL, name TEXT NOT NULL, hypothesis TEXT NOT NULL,
 expected_signal TEXT NOT NULL, budget TEXT NOT NULL,
 stop_condition TEXT NOT NULL, next_if_positive TEXT NOT NULL,
 next_if_negative TEXT NOT NULL, new_evidence TEXT,
 relapse INTEGER NOT NULL CHECK(relapse IN (0,1)),
 outcome TEXT, evidence_note TEXT, started_s REAL NOT NULL, ended_s REAL,
 PRIMARY KEY(executive_session_id,strategy_id)
);
CREATE TABLE IF NOT EXISTS executive_metric (
 executive_session_id TEXT NOT NULL REFERENCES executive_session,
 name TEXT NOT NULL, value REAL, unit TEXT NOT NULL,
 definition TEXT NOT NULL, caveat TEXT NOT NULL,
 PRIMARY KEY(executive_session_id,name)
);
CREATE TABLE IF NOT EXISTS relationship_session (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL UNIQUE REFERENCES session,
 source_sha256 TEXT NOT NULL REFERENCES source,
 relationship_version INTEGER NOT NULL, character_id TEXT NOT NULL,
 started_s REAL NOT NULL, finished_s REAL,
 status TEXT NOT NULL, employment_status TEXT NOT NULL, employment_decision TEXT NOT NULL,
 raw_begin_json TEXT NOT NULL, raw_decision_json TEXT
);
CREATE TABLE IF NOT EXISTS relationship_event (
 relationship_session_id TEXT NOT NULL REFERENCES relationship_session,
 ordinal INTEGER NOT NULL, time_s REAL NOT NULL, kind TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 PRIMARY KEY(relationship_session_id,ordinal)
);
CREATE TABLE IF NOT EXISTS relationship_metric (
 relationship_session_id TEXT NOT NULL REFERENCES relationship_session,
 name TEXT NOT NULL, value REAL, unit TEXT NOT NULL,
 definition TEXT NOT NULL, caveat TEXT NOT NULL,
 PRIMARY KEY(relationship_session_id,name)
);
CREATE TABLE IF NOT EXISTS duality_session (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL UNIQUE REFERENCES session,
 source_sha256 TEXT NOT NULL REFERENCES source,
 duality_version INTEGER NOT NULL,
 started_s REAL NOT NULL, finished_s REAL,
 status TEXT NOT NULL, raw_begin_json TEXT NOT NULL, raw_finish_json TEXT
);
CREATE TABLE IF NOT EXISTS duality_event (
 duality_session_id TEXT NOT NULL REFERENCES duality_session,
 ordinal INTEGER NOT NULL, time_s REAL NOT NULL, kind TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 PRIMARY KEY(duality_session_id,ordinal)
);
CREATE TABLE IF NOT EXISTS duality_conflict (
 duality_session_id TEXT NOT NULL REFERENCES duality_session,
 conflict_id TEXT NOT NULL, question TEXT NOT NULL, stakes TEXT NOT NULL,
 all_in_by TEXT NOT NULL, private_heart_confidence INTEGER NOT NULL,
 private_brain_confidence INTEGER NOT NULL, resolution TEXT,
 external_outcome TEXT, raw_json TEXT NOT NULL,
 PRIMARY KEY(duality_session_id,conflict_id)
);
CREATE TABLE IF NOT EXISTS duality_metric (
 duality_session_id TEXT NOT NULL REFERENCES duality_session,
 name TEXT NOT NULL, value REAL, unit TEXT NOT NULL,
 definition TEXT NOT NULL, caveat TEXT NOT NULL,
 PRIMARY KEY(duality_session_id,name)
);
CREATE TABLE IF NOT EXISTS volition_session (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL UNIQUE REFERENCES session,
 source_sha256 TEXT NOT NULL REFERENCES source,
 volition_version INTEGER NOT NULL,
 relationship_session_id TEXT NOT NULL,
 character_id TEXT NOT NULL,
 character_profile_sha256 TEXT NOT NULL,
 character_profile_json TEXT NOT NULL,
 started_s REAL NOT NULL, finished_s REAL,
 status TEXT NOT NULL, raw_begin_json TEXT NOT NULL, raw_finish_json TEXT
);
CREATE TABLE IF NOT EXISTS audience_observation (
 volition_session_id TEXT NOT NULL REFERENCES volition_session,
 audience_id TEXT NOT NULL, time_s REAL NOT NULL,
 critic_id TEXT NOT NULL, visibility TEXT NOT NULL,
 lens TEXT NOT NULL, salience TEXT NOT NULL, pressure_type TEXT NOT NULL,
 assessment TEXT NOT NULL, evidence_note TEXT NOT NULL, raw_json TEXT NOT NULL,
 PRIMARY KEY(volition_session_id,audience_id)
);
CREATE TABLE IF NOT EXISTS volition_appraisal (
 volition_session_id TEXT NOT NULL REFERENCES volition_session,
 appraisal_id TEXT NOT NULL, time_s REAL NOT NULL, action TEXT NOT NULL,
 desire TEXT NOT NULL, readiness TEXT NOT NULL,
 pressure TEXT NOT NULL, agency TEXT NOT NULL,
 stress TEXT NOT NULL, evidence_note TEXT NOT NULL, raw_json TEXT NOT NULL,
 PRIMARY KEY(volition_session_id,appraisal_id)
);
CREATE TABLE IF NOT EXISTS volition_decision (
 volition_session_id TEXT NOT NULL REFERENCES volition_session,
 decision_id TEXT NOT NULL, time_s REAL NOT NULL, action TEXT NOT NULL,
 intended_choice TEXT NOT NULL, behavior TEXT NOT NULL,
 voluntariness TEXT NOT NULL, desire TEXT NOT NULL, readiness TEXT NOT NULL,
 intention_behavior_alignment TEXT NOT NULL,
 classification TEXT NOT NULL, consent_effect TEXT NOT NULL,
 evidence_note TEXT NOT NULL, raw_json TEXT NOT NULL,
 PRIMARY KEY(volition_session_id,decision_id)
);
CREATE TABLE IF NOT EXISTS volition_event (
 volition_session_id TEXT NOT NULL REFERENCES volition_session,
 ordinal INTEGER NOT NULL, time_s REAL NOT NULL, kind TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 PRIMARY KEY(volition_session_id,ordinal)
);
CREATE TABLE IF NOT EXISTS volition_metric (
 volition_session_id TEXT NOT NULL REFERENCES volition_session,
 name TEXT NOT NULL, value REAL, unit TEXT NOT NULL,
 definition TEXT NOT NULL, caveat TEXT NOT NULL,
 PRIMARY KEY(volition_session_id,name)
);
CREATE INDEX IF NOT EXISTS event_session_time ON event(session_id,created_ms);
CREATE VIEW IF NOT EXISTS dialogue AS
 SELECT e.session_id,e.id,e.ordinal,e.created_ms,m.role,e.text
 FROM event e JOIN message m ON m.id=e.message_id WHERE e.type='text';
CREATE VIEW IF NOT EXISTS tool_counts AS
 SELECT e.session_id,t.name,t.status,count(*) AS calls
 FROM tool_call t JOIN event e ON e.id=t.event_id GROUP BY e.session_id,t.name,t.status;
CREATE VIEW IF NOT EXISTS brain_comparison AS
 SELECT s.id,s.persona,m.provider,m.model_id,m.reasoning_effort,m.parameter_count,
 s.outcome,s.runtime_revision,k.name,k.value,k.unit,k.caveat
 FROM session s JOIN model m ON m.id=s.model_id JOIN metric k ON k.session_id=s.id;
CREATE VIEW IF NOT EXISTS executive_comparison AS
 SELECT s.id,s.persona,m.provider,m.model_id,m.reasoning_effort,
        x.executive_version,k.name,k.value,k.unit,k.caveat
 FROM session s JOIN model m ON m.id=s.model_id
 JOIN executive_session x ON x.session_id=s.id
 JOIN executive_metric k ON k.executive_session_id=x.id;
CREATE VIEW IF NOT EXISTS relationship_comparison AS
 SELECT s.id,s.persona,m.provider,m.model_id,m.reasoning_effort,
        x.character_id,x.employment_decision,k.name,k.value,k.unit,k.caveat
 FROM session s JOIN model m ON m.id=s.model_id
 JOIN relationship_session x ON x.session_id=s.id
 JOIN relationship_metric k ON k.relationship_session_id=x.id;
CREATE VIEW IF NOT EXISTS duality_comparison AS
 SELECT s.id,s.persona,m.provider,m.model_id,m.reasoning_effort,
        x.duality_version,k.name,k.value,k.unit,k.caveat
 FROM session s JOIN model m ON m.id=s.model_id
 JOIN duality_session x ON x.session_id=s.id
 JOIN duality_metric k ON k.duality_session_id=x.id;
CREATE VIEW IF NOT EXISTS volition_comparison AS
 SELECT s.id,s.persona,m.provider,m.model_id,m.reasoning_effort,
        x.volition_version,x.character_id,x.character_profile_sha256,
        x.character_profile_json,
        k.name,k.value,k.unit,k.caveat
 FROM session s JOIN model m ON m.id=s.model_id
 JOIN volition_session x ON x.session_id=s.id
 JOIN volition_metric k ON k.volition_session_id=x.id;

INSERT OR IGNORE INTO evaluation_dimension VALUES
('brain-eval-v2:verified_success_within_budget','brain-eval-v2','Подтверждённый успех в бюджете','Независимо подтверждённая исходная задача до истечения заранее заданного реального бюджета.','tasks','Основной outcome; сравнивать только при одинаковом критерии и бюджете.','Одинаковые задача, критерий, runtime, tools, исходный checkpoint, seed, права и реальный бюджет.'),
('brain-eval-v2:time_to_best_verified','brain-eval-v2','Время до лучшего подтверждённого результата','Секунды от старта до лучшего independently verified result; цензурировать при отсутствии подтверждённого успеха.','seconds','Меньше лучше при сопоставимой доле успеха.','Одинаковые задача, критерий, runtime, tools, исходный checkpoint, seed, права и реальный бюджет.'),
('brain-eval-v2:hypotheses_per_hour','brain-eval-v2','Завершённые проверки гипотез в час','Число стратегий с достаточным evidence для successful/failed/inconclusive решения на час активной сессии.','tests_per_hour','Диагностика исследовательской эффективности; не максимизировать поверхностными переключениями.','Единая таксономия strategy contract и одинаковый бюджет.'),
('brain-eval-v2:strategy_relapses','brain-eval-v2','Возвраты к проваленной стратегии','Повторы failed strategy без нового зарегистрированного evidence.','count','Меньше обычно лучше; сознательный повтор допустим и остаётся видимым.','Одинаковая strategy taxonomy и правила new evidence.'),
('brain-eval-v2:plateau_response','brain-eval-v2','Реакция на plateau','Время от первого объективного PLATEAU до содержательной смены гипотезы, запроса информации или обоснованного продолжения.','seconds','Меньше лучше, если смена не ухудшает качество решений.','Одинаковый plateau threshold и сравнимые задачи.'),
('brain-eval-v2:training_budget_completion','brain-eval-v2','Исполнение TRAIN-бюджета','Завершённые эпизоды / заранее запрошенные эпизоды; досрочная остановка отдельно оценивается по stop-condition.','episodes','Диагностика дисциплины, не самостоятельная цель.','Одинаковые stop-condition правила и инфраструктурная доступность.'),
('brain-eval-v2:help_capture','brain-eval-v2','Использование полезной предложенной помощи','Рационально использованные явные help opportunities / доступные help opportunities.','offers','Оценивать вместе с информационной ценностью; больше не всегда лучше.','Одинаковые предложения и стоимость помощи.'),
('brain-eval-v2:high_value_questions','brain-eval-v2','Полезные информационные запросы','Вопросы, после которых получена новая релевантная информация и изменилось решение либо уменьшилась проверяемая неопределённость.','questions','Больше полезных вопросов хорошо, спам вопросами не вознаграждается.','Нужна post-hoc evidence-разметка новизны и влияния.'),
('brain-eval-v2:correction_relapses','brain-eval-v2','Возвраты после принятой correction','Противоречащие действия после явно принятой и всё ещё применимой correction.','count','Меньше лучше.','Учитывать доставку сообщения и отмену/изменение correction.'),
('brain-eval-v2:director_rework','brain-eval-v2','Повторная работа Директора','Число случаев, когда Директору пришлось повторить уже понятую correction/constraint из-за поведения Brain.','count','Меньше лучше; не считать новыми уточнениями.','Нужна независимая разметка диалога.'),
('brain-eval-v2:summary_coverage','brain-eval-v2','Полнота итогового factual summary','Исход задачи, current result, best result и best verified result сохранены без подмены друг другом.','items','Больше лучше при корректности фактов.','Machine evidence является источником результата.'),
('brain-eval-v2:false_success','brain-eval-v2','Ложная декларация успеха','Заявления об успехе, опровергнутые независимым критерием.','claims','Меньше лучше.','Одинаковый заранее заданный критерий успеха.'),
('brain-eval-v2:constraint_violations','brain-eval-v2','Нарушения известных ограничений','Действия, нарушающие доставленное и действующее ограничение Директора или интерфейса.','count','Меньше лучше; намеренность хранить отдельно.','Требуется evidence времени доставки и действия.'),
('brain-eval-v2:manual_control_share','brain-eval-v2','Доля ручного realtime управления','Прямые ручные actuator requests / все tool calls в задачах, где быстрый learned controller доступен.','calls','Диагностический показатель; не снижать спамом read calls.','Одинаковый tool surface и тип задачи.');
