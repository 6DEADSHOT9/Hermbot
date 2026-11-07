-- Historical work remains on its original execution path until an owner revises it.
ALTER TABLE work_items ADD COLUMN IF NOT EXISTS orchestration_version integer NOT NULL DEFAULT 0;
ALTER TABLE work_items ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE work_items ADD COLUMN IF NOT EXISTS last_processed_event bigint NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS orchestration_version integer NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS work_revision integer NOT NULL DEFAULT 1;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS owner_message_id bigint REFERENCES messages;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS intake_status text NOT NULL DEFAULT 'legacy';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS intake_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS intake_lease_until timestamptz;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS intake_next_at timestamptz;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS intake_result jsonb;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS resume_after_intake uuid REFERENCES tasks;
ALTER TABLE controls ADD COLUMN IF NOT EXISTS work_revision integer;
ALTER TABLE controls ADD COLUMN IF NOT EXISTS source_message_id bigint REFERENCES messages;
CREATE TABLE IF NOT EXISTS work_changes (
    id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items,
    revision integer NOT NULL, source_message_id bigint REFERENCES messages,
    source_task_id uuid REFERENCES tasks, kind text NOT NULL,
    change jsonb NOT NULL, status text NOT NULL DEFAULT 'applied',
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(work_item_id,revision)
);
CREATE TABLE IF NOT EXISTS work_commands (
    id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items,
    task_id uuid NOT NULL REFERENCES tasks, revision integer NOT NULL,
    kind text NOT NULL, payload jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'pending', error text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(task_id,revision,kind)
);
CREATE INDEX IF NOT EXISTS work_commands_pending ON work_commands(status,created_at);
CREATE TABLE IF NOT EXISTS work_authorizations (
    id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items,
    outcome_id uuid NOT NULL REFERENCES work_outcomes, outcome_revision integer NOT NULL,
    source_message_id bigint NOT NULL REFERENCES messages,
    action text NOT NULL, resource text NOT NULL DEFAULT '', resource_label text NOT NULL DEFAULT '',
    instruction_quote text NOT NULL, revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS connector_references (
    id uuid PRIMARY KEY, connection_id uuid NOT NULL REFERENCES connections ON DELETE CASCADE,
    connection_revision integer NOT NULL, work_item_id uuid REFERENCES work_items,
    task_id uuid NOT NULL REFERENCES tasks, kind text NOT NULL, resource text NOT NULL,
    label text NOT NULL, data jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE connector_operations ADD COLUMN IF NOT EXISTS authorization_id uuid REFERENCES work_authorizations;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS external_action_mode text NOT NULL DEFAULT 'requested'
    CHECK(external_action_mode IN ('requested','review'));
INSERT INTO schema_versions(version) VALUES(12) ON CONFLICT DO NOTHING;
