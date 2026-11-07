-- Presentation references only; domain records remain authoritative.
CREATE TABLE IF NOT EXISTS timeline_clocks (
    conversation_id uuid PRIMARY KEY REFERENCES conversations ON DELETE CASCADE,
    version bigint NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS timeline_entries (
    conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
    id text NOT NULL, position bigint NOT NULL, revision bigint NOT NULL,
    thread_id bigint, occurred_at timestamptz NOT NULL,
    signature text NOT NULL, deleted boolean NOT NULL DEFAULT false,
    PRIMARY KEY(conversation_id,id), UNIQUE(conversation_id,position)
);
CREATE INDEX IF NOT EXISTS timeline_changes ON timeline_entries(conversation_id,revision);
CREATE INDEX IF NOT EXISTS timeline_scope ON timeline_entries(conversation_id,thread_id,position);
CREATE TABLE IF NOT EXISTS timeline_dirty (
    transaction_id bigint NOT NULL, conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
    source text NOT NULL DEFAULT '*', PRIMARY KEY(transaction_id,conversation_id,source)
);
CREATE TABLE IF NOT EXISTS timeline_previews (
    attempt_id uuid PRIMARY KEY REFERENCES attempts ON DELETE CASCADE,
    task_id uuid NOT NULL REFERENCES tasks ON DELETE CASCADE,
    content text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE input_requests ADD COLUMN IF NOT EXISTS answer_message_id bigint REFERENCES messages;
UPDATE input_requests q SET answer_message_id=t.owner_message_id FROM tasks t
    WHERE t.request_key='question-answer:'||q.id::text AND q.answer_message_id IS NULL;

CREATE OR REPLACE FUNCTION timeline_mark_dirty() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item jsonb; cid uuid; tid uuid; mid bigint; source text := '*';
BEGIN
    IF TG_OP='DELETE' THEN item := to_jsonb(OLD); ELSE item := to_jsonb(NEW); END IF;
    IF TG_TABLE_NAME='message_reactions' THEN
        mid := (item->>'message_id')::bigint;
        SELECT conversation_id INTO cid FROM messages WHERE id=mid;
    ELSIF TG_TABLE_NAME='timeline_previews' THEN
        tid := (item->>'task_id')::uuid;
        IF TG_OP<>'DELETE' THEN source := item->>'attempt_id'; END IF;
        SELECT conversation_id INTO cid FROM tasks WHERE id=tid;
    ELSIF TG_TABLE_NAME IN ('input_requests','approvals','attempts') THEN
        tid := (item->>'task_id')::uuid;
        SELECT conversation_id INTO cid FROM tasks WHERE id=tid;
    ELSE cid := (item->>'conversation_id')::uuid;
    END IF;
    IF cid IS NOT NULL THEN
        INSERT INTO timeline_dirty VALUES(txid_current(),cid,source) ON CONFLICT DO NOTHING;
    END IF;
    RETURN NULL;
END $$;

DO $$ DECLARE tab text; BEGIN
    FOREACH tab IN ARRAY ARRAY['messages','tasks','attempts','input_requests','approvals','message_reactions','timeline_previews'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS timeline_dirty ON %I',tab);
        EXECUTE format('CREATE TRIGGER timeline_dirty AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION timeline_mark_dirty()',tab);
    END LOOP;
END $$;

-- The connection finalizer publishes this backfill in the same migration transaction.
INSERT INTO timeline_dirty(transaction_id,conversation_id)
    SELECT txid_current(),id FROM conversations WHERE NOT EXISTS(SELECT 1 FROM schema_versions WHERE version=23)
    ON CONFLICT DO NOTHING;
INSERT INTO schema_versions(version) VALUES(23) ON CONFLICT DO NOTHING;
