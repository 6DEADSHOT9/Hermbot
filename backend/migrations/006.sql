-- Keep connector admission restrictions across delegation and subsequent policy detachment.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS connector_restricted boolean NOT NULL DEFAULT false;
INSERT INTO schema_versions(version) VALUES(6) ON CONFLICT DO NOTHING;
