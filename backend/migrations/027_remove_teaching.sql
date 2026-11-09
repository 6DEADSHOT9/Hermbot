-- Teaching (recorded owner demonstrations) was removed. Its tables are dropped once; skills it
-- published are ordinary installed skills and stay. Recordings in object storage are purged on deploy.
DROP TABLE IF EXISTS teaching_publications, teaching_tests, teaching_refinements, teaching_revisions, teaching_chunks, teaching_sessions;
