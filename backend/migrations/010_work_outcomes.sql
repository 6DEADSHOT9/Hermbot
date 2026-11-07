ALTER TABLE work_items ADD COLUMN IF NOT EXISTS outcomes_version integer NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS pending_outcomes jsonb NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS work_outcomes (
    id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items,
    key text NOT NULL, title text NOT NULL, kind text NOT NULL,
    required boolean NOT NULL DEFAULT true, revision integer NOT NULL DEFAULT 1,
    source_task_id uuid NOT NULL REFERENCES tasks, source_message_id bigint REFERENCES messages,
    target jsonb NOT NULL DEFAULT '{}', dependencies jsonb NOT NULL DEFAULT '[]',
    status text NOT NULL DEFAULT 'pending', reason text NOT NULL DEFAULT '',
    evidence_strength text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(work_item_id,key)
);
CREATE TABLE IF NOT EXISTS task_outcomes (
    task_id uuid REFERENCES tasks, outcome_id uuid REFERENCES work_outcomes,
    outcome_revision integer NOT NULL, PRIMARY KEY(task_id,outcome_id)
);
CREATE TABLE IF NOT EXISTS outcome_evidence (
    id uuid PRIMARY KEY, outcome_id uuid NOT NULL REFERENCES work_outcomes,
    outcome_revision integer NOT NULL, task_id uuid NOT NULL REFERENCES tasks,
    kind text NOT NULL, reference text NOT NULL, strength text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(outcome_id,outcome_revision,kind,reference)
);
ALTER TABLE connector_operations ADD COLUMN IF NOT EXISTS outcome_id uuid REFERENCES work_outcomes;
ALTER TABLE connector_operations ADD COLUMN IF NOT EXISTS outcome_revision integer;
CREATE TABLE IF NOT EXISTS input_requests (
    id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks,
    work_item_id uuid REFERENCES work_items, outcome_id uuid REFERENCES work_outcomes,
    outcome_revision integer, prompt text NOT NULL, answer_type text NOT NULL,
    choices jsonb NOT NULL DEFAULT '[]', question_hash text NOT NULL,
    status text NOT NULL DEFAULT 'pending', answer text, answer_source text,
    created_at timestamptz NOT NULL DEFAULT now(), answered_at timestamptz,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS input_requests_pending ON input_requests(task_id,status);
CREATE INDEX IF NOT EXISTS work_outcomes_item ON work_outcomes(work_item_id);
ALTER TABLE work_outcomes ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0;
INSERT INTO schema_versions(version) VALUES(10) ON CONFLICT DO NOTHING;
