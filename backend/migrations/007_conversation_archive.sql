-- Remove conversations from the product roster without destroying work/evidence.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS archived_at timestamptz;
INSERT INTO schema_versions(version) VALUES(7) ON CONFLICT DO NOTHING;
