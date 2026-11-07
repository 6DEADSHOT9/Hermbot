CREATE TABLE IF NOT EXISTS teaching_refinements (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES teaching_sessions ON DELETE CASCADE,
 base_draft_hash text NOT NULL, evidence_hash text NOT NULL, source_hash text NOT NULL,
 sources_encrypted text NOT NULL, result_encrypted text,
 state text NOT NULL DEFAULT 'queued', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(session_id,source_hash)
);
ALTER TABLE teaching_sessions ADD COLUMN IF NOT EXISTS learning_encrypted text;
ALTER TABLE teaching_tests ADD COLUMN IF NOT EXISTS auto_refine boolean NOT NULL DEFAULT false;
ALTER TABLE teaching_tests ADD COLUMN IF NOT EXISTS refinement_id uuid REFERENCES teaching_refinements;
INSERT INTO schema_versions(version) VALUES(19) ON CONFLICT DO NOTHING;
