-- Existing executions and receipts retain their original semantics.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS coordination_version integer NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_kind text NOT NULL DEFAULT 'work';
ALTER TABLE work_outcomes ADD COLUMN IF NOT EXISTS owner_task_id uuid REFERENCES tasks;
CREATE TABLE IF NOT EXISTS teammate_messages (
    id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations,
    work_item_id uuid REFERENCES work_items, source_task_id uuid NOT NULL REFERENCES tasks,
    sender text NOT NULL REFERENCES bots, recipient text NOT NULL REFERENCES bots,
    content text NOT NULL, reply_to uuid REFERENCES teammate_messages,
    status text NOT NULL DEFAULT 'pending', delivered_attempt_id uuid REFERENCES attempts,
    response_task_id uuid REFERENCES tasks, created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz, UNIQUE(source_task_id,id)
);
CREATE INDEX IF NOT EXISTS teammate_inbox ON teammate_messages(recipient,conversation_id,status,created_at);
CREATE TABLE IF NOT EXISTS result_contributions (
    task_id uuid NOT NULL REFERENCES tasks, outcome_id uuid NOT NULL REFERENCES work_outcomes,
    outcome_revision integer NOT NULL, required boolean NOT NULL DEFAULT true,
    expected_output text NOT NULL, status text NOT NULL DEFAULT 'pending',
    submission jsonb NOT NULL DEFAULT '{}', reviewed_by uuid REFERENCES tasks,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(task_id,outcome_id,outcome_revision)
);
CREATE TABLE IF NOT EXISTS result_acceptances (
    id uuid PRIMARY KEY, outcome_id uuid NOT NULL REFERENCES work_outcomes,
    outcome_revision integer NOT NULL, task_id uuid NOT NULL REFERENCES tasks,
    attempt_id uuid REFERENCES attempts, evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(outcome_id,outcome_revision)
);
CREATE TABLE IF NOT EXISTS coordination_waits (
    task_id uuid NOT NULL REFERENCES tasks, dependency_id uuid REFERENCES tasks,
    message_id uuid REFERENCES teammate_messages,
    CHECK ((dependency_id IS NULL) <> (message_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS wait_assignment ON coordination_waits(task_id,dependency_id) WHERE dependency_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS wait_message ON coordination_waits(task_id,message_id) WHERE message_id IS NOT NULL;
INSERT INTO schema_versions(version) VALUES(13) ON CONFLICT DO NOTHING;
