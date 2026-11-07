CREATE TABLE IF NOT EXISTS computer_controls (
    bot_id text PRIMARY KEY REFERENCES bots ON DELETE CASCADE,
    holder text NOT NULL DEFAULT 'agent' CHECK(holder IN ('agent','human')),
    viewer_hash text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS knowledge_versions (
    id uuid PRIMARY KEY,
    bot_id text NOT NULL REFERENCES bots ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('memory','skill')),
    name text NOT NULL,
    version integer NOT NULL CHECK(version>0),
    content text NOT NULL,
    sha256 text NOT NULL,
    source_task_id uuid REFERENCES tasks ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(bot_id,kind,name,version)
);
CREATE TABLE IF NOT EXISTS knowledge_proposals (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES tasks ON DELETE CASCADE,
    approval_id uuid NOT NULL UNIQUE REFERENCES approvals ON DELETE CASCADE,
    bot_id text NOT NULL REFERENCES bots,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    error text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS supersedes uuid REFERENCES approvals;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS superseded_by uuid REFERENCES approvals;
DO $$ BEGIN
IF NOT EXISTS(SELECT 1 FROM schema_versions WHERE version=4) THEN
    UPDATE workspace_settings SET permitted_tools=permitted_tools||'["memory","browser","computer_use"]'::jsonb;
END IF;
END $$;
INSERT INTO schema_versions(version) VALUES(4) ON CONFLICT DO NOTHING;
