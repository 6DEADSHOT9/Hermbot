-- A retry records the task its work started as, so "a retry of this work is already running" means
-- exactly that, not another piece of the same owner message that happens to share a grant.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS retried_from uuid REFERENCES tasks ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS task_retried_from ON tasks(retried_from) WHERE retried_from IS NOT NULL;
-- Grant lookups by root task: the shared handoff budget, revocation and retry checks.
CREATE INDEX IF NOT EXISTS task_root_grant ON tasks(grant_id) WHERE parent_id IS NULL AND grant_id IS NOT NULL;
