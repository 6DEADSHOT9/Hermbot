-- Phase 1 one-shot routines retain their existing admission and settlement contract.
CREATE TABLE IF NOT EXISTS routine_definitions (
 id uuid PRIMARY KEY, revision integer NOT NULL DEFAULT 1,
 bot_id text NOT NULL REFERENCES bots, conversation_id uuid NOT NULL REFERENCES conversations,
 state text NOT NULL CHECK(state IN ('draft','active','paused','archived','finished')),
 replaces_id uuid REFERENCES routine_definitions, replaces_revision integer,
 next_run_at timestamptz, checked_at timestamptz, error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS routine_revisions (
 routine_id uuid NOT NULL REFERENCES routine_definitions, revision integer NOT NULL,
 definition jsonb NOT NULL, source_message_id bigint NOT NULL REFERENCES messages,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(routine_id,revision)
);
CREATE TABLE IF NOT EXISTS routine_runs (
 id uuid PRIMARY KEY, routine_id uuid NOT NULL REFERENCES routine_definitions,
 revision integer NOT NULL, occurrence_key text NOT NULL, scheduled_at timestamptz NOT NULL,
 origin text NOT NULL CHECK(origin IN ('scheduled','manual')),
 status text NOT NULL CHECK(status IN ('admitted','skipped')), reason text NOT NULL DEFAULT '',
 task_id uuid UNIQUE REFERENCES tasks, start_before timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(routine_id,occurrence_key),
 FOREIGN KEY(routine_id,revision) REFERENCES routine_revisions(routine_id,revision)
);
CREATE INDEX IF NOT EXISTS routines_due ON routine_definitions(next_run_at) WHERE state='active';
CREATE INDEX IF NOT EXISTS routine_run_history ON routine_runs(routine_id,created_at DESC,id);
CREATE TABLE IF NOT EXISTS routine_schedulers (
 bot_id text PRIMARY KEY REFERENCES bots, token_hash text NOT NULL, token_encrypted text NOT NULL,
 generation uuid NOT NULL, runtime_job_id text, last_tick_at timestamptz,
 checked_at timestamptz, error text, enabled boolean NOT NULL DEFAULT true
);
ALTER TABLE routine_runs ADD COLUMN IF NOT EXISTS context jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS routine_run_id uuid REFERENCES routine_runs;
INSERT INTO schema_versions(version) VALUES(14) ON CONFLICT DO NOTHING;
