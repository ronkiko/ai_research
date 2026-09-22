PRAGMA foreign_keys=ON;
PRAGMA user_version=1;
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
