CREATE TABLE IF NOT EXISTS bots (
    id text PRIMARY KEY, name text NOT NULL, role text NOT NULL, color text NOT NULL,
    capabilities jsonb NOT NULL, runtime_url text NOT NULL, readiness jsonb NOT NULL DEFAULT '{}',
    checked_at timestamptz
);
CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY, title text NOT NULL, kind text NOT NULL CHECK (kind IN ('dm','group')),
    bot_id text REFERENCES bots, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS participants (
    conversation_id uuid REFERENCES conversations ON DELETE CASCADE, bot_id text REFERENCES bots,
    PRIMARY KEY(conversation_id, bot_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    conversation_id uuid REFERENCES conversations, bot_id text REFERENCES bots,
    runtime_session_id text NOT NULL UNIQUE, PRIMARY KEY(conversation_id, bot_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_dm_per_bot ON conversations(bot_id) WHERE kind='dm';
CREATE TABLE IF NOT EXISTS grants (
    id uuid PRIMARY KEY, manager_id text REFERENCES bots NOT NULL, targets jsonb NOT NULL,
    operations jsonb NOT NULL, max_handoffs integer NOT NULL, expires_at timestamptz NOT NULL,
    revoked_at timestamptz, root_id uuid NOT NULL, initiating_user text NOT NULL DEFAULT 'owner'
);
CREATE TABLE IF NOT EXISTS tasks (
    id uuid PRIMARY KEY, bot_id text REFERENCES bots NOT NULL, conversation_id uuid REFERENCES conversations NOT NULL,
    parent_id uuid REFERENCES tasks, root_id uuid NOT NULL, grant_id uuid REFERENCES grants,
    input text NOT NULL, status text NOT NULL, output text, error text, depth integer NOT NULL DEFAULT 0,
    handoffs integer NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0,
    context text NOT NULL DEFAULT 'chat', continuation text, pending_result text,
    initiating_user text NOT NULL DEFAULT 'owner', created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(), deadline timestamptz NOT NULL,
    request_key text UNIQUE, request_hash text, observed_terminal_children text
);
CREATE INDEX IF NOT EXISTS task_queue ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS task_root ON tasks(root_id);
CREATE INDEX IF NOT EXISTS task_parent ON tasks(parent_id);
CREATE TABLE IF NOT EXISTS dependencies (
    task_id uuid REFERENCES tasks, depends_on uuid REFERENCES tasks,
    PRIMARY KEY(task_id, depends_on), CHECK (task_id <> depends_on)
);
CREATE TABLE IF NOT EXISTS attempts (
    id uuid PRIMARY KEY, task_id uuid REFERENCES tasks NOT NULL, bot_id text REFERENCES bots NOT NULL,
    number integer NOT NULL, session_id text NOT NULL, status text NOT NULL,
    runtime_run_id text, idempotency_key text NOT NULL UNIQUE, input text NOT NULL,
    response jsonb NOT NULL DEFAULT '{}', last_event_id text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    worker_id text, lease_until timestamptz, UNIQUE(task_id, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_bot ON attempts(bot_id)
    WHERE status IN ('dispatching','running','waiting_for_approval','stopping','uncertain','external','reserved');
CREATE TABLE IF NOT EXISTS messages (
    id bigserial PRIMARY KEY, conversation_id uuid REFERENCES conversations NOT NULL,
    author text NOT NULL, role text NOT NULL, content text NOT NULL, task_id uuid REFERENCES tasks,
    source_id text UNIQUE, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY, kind text NOT NULL, conversation_id uuid REFERENCES conversations,
    task_id uuid REFERENCES tasks, bot_id text REFERENCES bots, data jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(), source_id text UNIQUE
);
CREATE TABLE IF NOT EXISTS tool_receipts (
    id text PRIMARY KEY, bot_id text REFERENCES bots NOT NULL, task_id uuid REFERENCES tasks NOT NULL,
    operation text NOT NULL, request_hash text NOT NULL, response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS approvals (
    id uuid PRIMARY KEY, task_id uuid REFERENCES tasks NOT NULL, attempt_id uuid REFERENCES attempts,
    kind text NOT NULL, payload jsonb NOT NULL, payload_hash text NOT NULL, decision text,
    status text NOT NULL DEFAULT 'pending', created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL, decided_at timestamptz, source_id text UNIQUE
);
CREATE TABLE IF NOT EXISTS controls (
    id uuid PRIMARY KEY, task_id uuid REFERENCES tasks NOT NULL, attempt_id uuid REFERENCES attempts,
    input text NOT NULL, status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artifacts (
    id uuid PRIMARY KEY, task_id uuid REFERENCES tasks NOT NULL, bot_id text REFERENCES bots NOT NULL,
    name text NOT NULL, content_type text NOT NULL, object_key text NOT NULL UNIQUE,
    sha256 text NOT NULL, size integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artifact_access (
    artifact_id uuid REFERENCES artifacts, task_id uuid REFERENCES tasks,
    PRIMARY KEY(artifact_id, task_id)
);
CREATE TABLE IF NOT EXISTS routines (
    id uuid PRIMARY KEY, bot_id text REFERENCES bots NOT NULL, conversation_id uuid REFERENCES conversations NOT NULL,
    input text NOT NULL, runtime_job_id text UNIQUE, status text NOT NULL DEFAULT 'creating',
    scheduled_at timestamptz NOT NULL, task_id uuid REFERENCES tasks, dispatch_ticket text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS schema_versions (version integer PRIMARY KEY, applied_at timestamptz DEFAULT now());
INSERT INTO schema_versions(version) VALUES (1) ON CONFLICT DO NOTHING;
