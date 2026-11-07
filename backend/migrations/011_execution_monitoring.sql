ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS active_budget_seconds integer NOT NULL DEFAULT 3600 CHECK(active_budget_seconds BETWEEN 60 AND 86400);
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS decision_provider_id uuid REFERENCES providers ON DELETE SET NULL;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS decision_model text NOT NULL DEFAULT '';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS decision_mode text NOT NULL DEFAULT 'disabled' CHECK(decision_mode IN ('disabled','decisions','chat'));
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS decision_actions boolean NOT NULL DEFAULT false;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS execution_policy_revision integer NOT NULL DEFAULT 1;
ALTER TABLE work_items ADD COLUMN IF NOT EXISTS active_budget_seconds integer NOT NULL DEFAULT 3600;
ALTER TABLE tasks ALTER COLUMN deadline DROP NOT NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_policy_version integer NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS active_budget_seconds integer NOT NULL DEFAULT 3600;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS recovery_failures integer NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS stop_kind text;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS stop_reason text;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS active_seconds double precision NOT NULL DEFAULT 0;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS accounted_at timestamptz;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS last_contact_at timestamptz;
CREATE TABLE IF NOT EXISTS execution_assessments (
    id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES tasks, attempt_id uuid NOT NULL REFERENCES attempts,
    evidence_hash text NOT NULL, evidence jsonb NOT NULL, policy_revision integer NOT NULL,
    provider_id uuid REFERENCES providers ON DELETE SET NULL, provider_revision integer, model text NOT NULL,
    mode text NOT NULL, status text NOT NULL DEFAULT 'queued', recommendation text,
    confidence double precision, error text, usage jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz, UNIQUE(attempt_id,evidence_hash,policy_revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_assessment ON execution_assessments(attempt_id) WHERE status IN ('queued','evaluating');
ALTER TABLE execution_assessments DROP CONSTRAINT IF EXISTS execution_assessments_provider_id_fkey;
ALTER TABLE execution_assessments ADD CONSTRAINT execution_assessments_provider_id_fkey FOREIGN KEY(provider_id) REFERENCES providers ON DELETE SET NULL;
ALTER TABLE controls ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'owner';
INSERT INTO schema_versions(version) VALUES (11) ON CONFLICT DO NOTHING;
