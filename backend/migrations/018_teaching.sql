CREATE TABLE IF NOT EXISTS teaching_sessions (
 id uuid PRIMARY KEY, bot_id text NOT NULL REFERENCES bots(id), revision integer NOT NULL DEFAULT 1,
 capture_revision integer NOT NULL DEFAULT 0, viewer_hash text NOT NULL, epoch bigint NOT NULL,
 state text NOT NULL DEFAULT 'preparing', reason text NOT NULL DEFAULT '', last_seq bigint NOT NULL DEFAULT 0,
 retained_bytes bigint NOT NULL DEFAULT 0, info_encrypted text NOT NULL,
 evidence_encrypted text, evidence_hash text, draft_encrypted text, draft_hash text,
 skill_name text, job_state text NOT NULL DEFAULT 'idle', job_token uuid, job_started_at timestamptz,
 job_provider jsonb, error text, pinned boolean NOT NULL DEFAULT false,
 expires_at timestamptz NOT NULL DEFAULT now()+interval '7 days',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS teaching_active_bot ON teaching_sessions(bot_id) WHERE state IN ('preparing','recording','paused','finalizing');
CREATE TABLE IF NOT EXISTS teaching_chunks (
 session_id uuid NOT NULL REFERENCES teaching_sessions ON DELETE CASCADE,
 first_seq bigint NOT NULL,last_seq bigint NOT NULL,object_key text NOT NULL UNIQUE,
 sha256 text NOT NULL,size integer NOT NULL,PRIMARY KEY(session_id,first_seq)
);
CREATE TABLE IF NOT EXISTS teaching_revisions (
 session_id uuid NOT NULL REFERENCES teaching_sessions ON DELETE CASCADE,
 kind text NOT NULL CHECK(kind IN ('evidence','draft')), sha256 text NOT NULL,
 data_encrypted text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(session_id,kind,sha256)
);
CREATE TABLE IF NOT EXISTS teaching_tests (
 id uuid PRIMARY KEY,session_id uuid NOT NULL REFERENCES teaching_sessions ON DELETE CASCADE,
 task_id uuid NOT NULL UNIQUE REFERENCES tasks ON DELETE CASCADE,
 evidence_hash text NOT NULL,draft_hash text NOT NULL,candidate_encrypted text NOT NULL,
 skill_name text NOT NULL,config_hash text NOT NULL,checks_encrypted text NOT NULL,
 consulted_at timestamptz,owner_passed boolean NOT NULL DEFAULT false, report_encrypted text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS teaching_publications (
 id uuid PRIMARY KEY,session_id uuid NOT NULL REFERENCES teaching_sessions,
 test_id uuid NOT NULL REFERENCES teaching_tests,knowledge_version_id uuid NOT NULL REFERENCES knowledge_versions,
 evidence_hash text NOT NULL,draft_hash text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(session_id,draft_hash,test_id)
);
INSERT INTO schema_versions(version) VALUES(18) ON CONFLICT DO NOTHING;
