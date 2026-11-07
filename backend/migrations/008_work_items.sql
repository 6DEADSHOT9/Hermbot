CREATE TABLE IF NOT EXISTS work_items (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations,
    bot_id text NOT NULL REFERENCES bots,
    title text NOT NULL,
    objective text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    hidden_at timestamptz,
    legacy boolean NOT NULL DEFAULT false
);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS work_item_id uuid REFERENCES work_items;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS message_intent text NOT NULL DEFAULT 'assignment';
CREATE INDEX IF NOT EXISTS tasks_work_item ON tasks(work_item_id);
CREATE TABLE IF NOT EXISTS work_item_messages (
    message_id bigint PRIMARY KEY REFERENCES messages,
    work_item_id uuid NOT NULL REFERENCES work_items,
    intent text NOT NULL
);
DO $$ BEGIN
    IF NOT EXISTS(SELECT 1 FROM schema_versions WHERE version=8) THEN
        -- Retain old assignments as reversible associations. No run is submitted or replayed.
        INSERT INTO work_items(id,conversation_id,bot_id,title,objective,created_at,updated_at,legacy)
        SELECT id,conversation_id,bot_id,left(input,120),input,created_at,updated_at,true
        FROM tasks WHERE parent_id IS NULL;
        UPDATE tasks SET work_item_id=root_id;
        INSERT INTO work_item_messages(message_id,work_item_id,intent)
        SELECT m.id,t.work_item_id,'assignment' FROM messages m JOIN tasks t ON t.id=m.task_id WHERE m.role='user';
    END IF;
END $$;
INSERT INTO schema_versions(version) VALUES(8) ON CONFLICT DO NOTHING;
