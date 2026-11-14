ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS limit_overrides jsonb NOT NULL DEFAULT '{}';
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS limits_revision integer NOT NULL DEFAULT 1;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS machine_facts jsonb NOT NULL DEFAULT '{}';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS limit_overrides jsonb NOT NULL DEFAULT '{}';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS applied_limits_revision integer NOT NULL DEFAULT 0;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS limits_observed jsonb NOT NULL DEFAULT '{}';
