ALTER TABLE artifacts ALTER COLUMN task_id DROP NOT NULL;
ALTER TABLE artifacts ALTER COLUMN bot_id DROP NOT NULL;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS conversation_id uuid REFERENCES conversations;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS family_id uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1 CHECK(version>0);
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS previous_id uuid REFERENCES artifacts;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'bot';
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS preview_text text NOT NULL DEFAULT '';
UPDATE artifacts a SET conversation_id=t.conversation_id FROM tasks t WHERE a.task_id=t.id AND a.conversation_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS artifact_version ON artifacts(family_id,version);
CREATE INDEX IF NOT EXISTS artifact_conversation ON artifacts(conversation_id,created_at);
CREATE TABLE IF NOT EXISTS library_shares (
    artifact_id uuid REFERENCES artifacts ON DELETE CASCADE,
    conversation_id uuid REFERENCES conversations ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(artifact_id,conversation_id)
);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS library_artifact_ids jsonb NOT NULL DEFAULT '[]';
INSERT INTO schema_versions(version) VALUES(3) ON CONFLICT DO NOTHING;
