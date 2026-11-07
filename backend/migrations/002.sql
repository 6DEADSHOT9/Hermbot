CREATE TABLE IF NOT EXISTS providers (
    id uuid PRIMARY KEY, name text NOT NULL UNIQUE, base_url text NOT NULL,
    models jsonb NOT NULL, default_model text NOT NULL, secret_encrypted text NOT NULL,
    revision integer NOT NULL DEFAULT 1, from_environment boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS workspace_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), timezone text NOT NULL DEFAULT 'UTC',
    default_provider_id uuid REFERENCES providers, permitted_tools jsonb NOT NULL DEFAULT '["file","terminal","skills"]',
    max_bots integer NOT NULL DEFAULT 10 CHECK(max_bots BETWEEN 3 AND 30), max_active integer NOT NULL DEFAULT 3 CHECK(max_active BETWEEN 1 AND 10)
);
INSERT INTO workspace_settings(singleton) VALUES(true) ON CONFLICT DO NOTHING;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS environment_imported boolean NOT NULL DEFAULT false;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS desired_state text NOT NULL DEFAULT 'running';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS applied_revision integer NOT NULL DEFAULT 0;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS runtime_error text;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS avatar text NOT NULL DEFAULT '';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS provider_id uuid REFERENCES providers;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS model_name text NOT NULL DEFAULT '';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS enabled_tools jsonb NOT NULL DEFAULT '["file","terminal"]';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS enabled_skills jsonb NOT NULL DEFAULT '[]';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS memory_mb integer NOT NULL DEFAULT 2048;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS cpus numeric NOT NULL DEFAULT 1.5;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS bot_token_encrypted text;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS bot_token_hash text;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS runtime_key_encrypted text;
CREATE UNIQUE INDEX IF NOT EXISTS bot_credential_hash ON bots(bot_token_hash);
CREATE TABLE IF NOT EXISTS owner_sessions (
    token_hash text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL, revoked_at timestamptz
);
CREATE TABLE IF NOT EXISTS owner_receipts (
    key text PRIMARY KEY, request_hash text NOT NULL, response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE grants ALTER COLUMN root_id DROP NOT NULL;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS max_created_bots integer NOT NULL DEFAULT 0;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS created_bots integer NOT NULL DEFAULT 0;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS max_memory_mb integer NOT NULL DEFAULT 2048;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS max_cpus numeric NOT NULL DEFAULT 1.5;
ALTER TABLE grants ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'task';
CREATE TABLE IF NOT EXISTS bot_operations (
    id uuid PRIMARY KEY, bot_id text REFERENCES bots NOT NULL, action text NOT NULL,
    status text NOT NULL DEFAULT 'queued', payload jsonb NOT NULL, request_key text NOT NULL UNIQUE,
    request_hash text NOT NULL, error text, result jsonb, initiating_user text NOT NULL DEFAULT 'owner',
    proposal_id uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    worker_id text, lease_until timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_bot_operation ON bot_operations(bot_id) WHERE status IN ('queued','applying','uncertain');
CREATE TABLE IF NOT EXISTS lifecycle_proposals (
    id uuid PRIMARY KEY, task_id uuid REFERENCES tasks NOT NULL, grant_id uuid REFERENCES grants NOT NULL,
    approval_id uuid REFERENCES approvals NOT NULL, action text NOT NULL, bot_id text,
    status text NOT NULL DEFAULT 'pending', payload jsonb NOT NULL, expected_revision integer,
    operation_id uuid REFERENCES bot_operations, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS attachments (
    id uuid PRIMARY KEY, conversation_id uuid REFERENCES conversations NOT NULL, name text NOT NULL,
    content_type text NOT NULL, size integer NOT NULL, sha256 text NOT NULL, object_key text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS attachment_ids jsonb NOT NULL DEFAULT '[]';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reply_to_message_id bigint;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS skill_refs jsonb NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_message_id bigint REFERENCES messages;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachment_ids jsonb NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS mentions jsonb NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at timestamptz;
CREATE TABLE IF NOT EXISTS message_reactions (
    message_id bigint REFERENCES messages ON DELETE CASCADE, emoji text NOT NULL,
    author text NOT NULL DEFAULT 'owner', PRIMARY KEY(message_id,emoji,author)
);
CREATE TABLE IF NOT EXISTS thread_sessions (
    message_id bigint REFERENCES messages, bot_id text REFERENCES bots, runtime_session_id text NOT NULL UNIQUE,
    PRIMARY KEY(message_id,bot_id)
);
INSERT INTO schema_versions(version) VALUES(2) ON CONFLICT DO NOTHING;
