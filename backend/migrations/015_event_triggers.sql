-- Event reception is durable independently of a bot, browser or collector process.
CREATE TABLE IF NOT EXISTS event_sources (
 id uuid PRIMARY KEY, name text NOT NULL, provider text NOT NULL,
 connection_id uuid REFERENCES connections, connection_revision integer,
 transport text NOT NULL CHECK(transport IN ('poll','socket','http')),
 resource text NOT NULL DEFAULT '', revision integer NOT NULL DEFAULT 1,
 enabled boolean NOT NULL DEFAULT true, status text NOT NULL DEFAULT 'initializing',
 remote_identity jsonb NOT NULL DEFAULT '{}', secret_encrypted text,
 previous_secret_encrypted text, previous_secret_until timestamptz,
 cursor_encrypted text, progress_encrypted text, initialized_at timestamptz,
 poll_interval_seconds integer NOT NULL DEFAULT 60 CHECK(poll_interval_seconds BETWEEN 60 AND 3600),
 next_poll_at timestamptz NOT NULL DEFAULT now(), failures integer NOT NULL DEFAULT 0,
 lease_owner uuid, lease_until timestamptz, generation bigint NOT NULL DEFAULT 1,
 checked_at timestamptz, last_event_at timestamptz, connected_at timestamptz,
 gap_since timestamptz, error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS event_source_live_scope ON event_sources(connection_id,provider,resource) WHERE connection_id IS NOT NULL AND status<>'archived';
CREATE UNIQUE INDEX IF NOT EXISTS event_slack_live_installation ON event_sources((remote_identity->>'team_id'),(remote_identity->>'app_id')) WHERE provider='slack' AND remote_identity ? 'app_id' AND status<>'archived';
CREATE UNIQUE INDEX IF NOT EXISTS event_slack_live_socket_app ON event_sources((remote_identity->>'app_id')) WHERE provider='slack' AND transport='socket' AND remote_identity ? 'app_id' AND status<>'archived';
DROP INDEX IF EXISTS event_source_connection_scope;
DROP INDEX IF EXISTS event_slack_installation;
DROP INDEX IF EXISTS event_slack_socket_app;
CREATE TABLE IF NOT EXISTS event_trigger_definitions (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES event_sources,
 bot_id text NOT NULL REFERENCES bots, conversation_id uuid NOT NULL REFERENCES conversations,
 revision integer NOT NULL DEFAULT 1,
 state text NOT NULL CHECK(state IN ('draft','active','paused','archived')),
 activated_at timestamptz, error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS event_trigger_revisions (
 trigger_id uuid NOT NULL REFERENCES event_trigger_definitions, revision integer NOT NULL,
 definition jsonb NOT NULL, source_message_id bigint NOT NULL REFERENCES messages,
 source_revision integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(trigger_id,revision)
);
CREATE TABLE IF NOT EXISTS trigger_events (
 id uuid PRIMARY KEY, source_id uuid NOT NULL REFERENCES event_sources,
 source_revision integer NOT NULL, event_key text NOT NULL,
 event_type text NOT NULL, resource text NOT NULL DEFAULT '', resource_version text NOT NULL DEFAULT '',
 actor text NOT NULL DEFAULT '', occurred_at timestamptz NOT NULL,
 payload_encrypted text, summary text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}',
 received_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source_id,event_key)
);
CREATE INDEX IF NOT EXISTS trigger_event_history ON trigger_events(source_id,received_at DESC,id);
CREATE TABLE IF NOT EXISTS trigger_deliveries (
 source_id uuid NOT NULL REFERENCES event_sources, delivery_key text NOT NULL,
 body_hash text NOT NULL, event_id uuid REFERENCES trigger_events,
 disposition text NOT NULL, duplicate_count integer NOT NULL DEFAULT 0,
 source_revision integer, payload_encrypted text, headers jsonb NOT NULL DEFAULT '{}', error text,
 received_at timestamptz NOT NULL DEFAULT now(), last_received_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(source_id,delivery_key)
);
CREATE INDEX IF NOT EXISTS trigger_delivery_history ON trigger_deliveries(source_id,received_at DESC,delivery_key);
CREATE TABLE IF NOT EXISTS trigger_resource_versions (
 source_id uuid NOT NULL REFERENCES event_sources, resource text NOT NULL,
 version text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}',
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(source_id,resource)
);
CREATE TABLE IF NOT EXISTS trigger_runs (
 id uuid PRIMARY KEY, trigger_id uuid NOT NULL REFERENCES event_trigger_definitions,
 revision integer NOT NULL, occurrence_key text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','admitted','ignored','cancelled','expired','failed')),
 reason text NOT NULL DEFAULT '', task_id uuid UNIQUE REFERENCES tasks,
 ready_at timestamptz NOT NULL, start_before timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(trigger_id,occurrence_key),
 FOREIGN KEY(trigger_id,revision) REFERENCES event_trigger_revisions(trigger_id,revision)
);
CREATE INDEX IF NOT EXISTS trigger_pending_runs ON trigger_runs(ready_at,created_at) WHERE status='pending';
CREATE INDEX IF NOT EXISTS trigger_run_history ON trigger_runs(trigger_id,created_at DESC,id);
ALTER TABLE trigger_runs ADD COLUMN IF NOT EXISTS admitted_at timestamptz;
UPDATE trigger_runs SET admitted_at=updated_at WHERE status='admitted' AND admitted_at IS NULL;
CREATE TABLE IF NOT EXISTS trigger_matches (
 trigger_id uuid NOT NULL REFERENCES event_trigger_definitions,
 event_id uuid NOT NULL REFERENCES trigger_events, revision integer NOT NULL,
 run_id uuid REFERENCES trigger_runs, matched boolean NOT NULL, reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(trigger_id,event_id),
 FOREIGN KEY(trigger_id,revision) REFERENCES event_trigger_revisions(trigger_id,revision)
);
CREATE INDEX IF NOT EXISTS trigger_match_run ON trigger_matches(run_id,event_id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS trigger_run_id uuid REFERENCES trigger_runs;
INSERT INTO schema_versions(version) VALUES(15) ON CONFLICT DO NOTHING;
