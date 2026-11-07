-- Scope belongs to the occurrence's work, including replacement roots and peer replies.
ALTER TABLE work_items ADD COLUMN IF NOT EXISTS management_scope jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS management_work_item_id uuid REFERENCES work_items;
CREATE INDEX IF NOT EXISTS task_management_work ON tasks(management_work_item_id) WHERE management_work_item_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS work_management_grant ON work_items ((management_scope->>'grant_id'))
 WHERE management_scope <> '{}'::jsonb;
INSERT INTO schema_versions(version) VALUES(16) ON CONFLICT DO NOTHING;
