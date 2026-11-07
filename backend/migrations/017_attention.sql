CREATE TABLE IF NOT EXISTS notification_preferences (
 owner text PRIMARY KEY DEFAULT 'owner' CHECK(owner='owner'), revision integer NOT NULL DEFAULT 1,
 push_enabled boolean NOT NULL DEFAULT false, categories jsonb NOT NULL DEFAULT '["questions","reviews","results","problems"]',
 timezone text NOT NULL DEFAULT 'UTC', quiet_enabled boolean NOT NULL DEFAULT false,
 quiet_start time NOT NULL DEFAULT '22:00', quiet_end time NOT NULL DEFAULT '08:00',
 initialized_at timestamptz NOT NULL DEFAULT now(), heartbeat_at timestamptz,
 reconcile_cursor jsonb NOT NULL DEFAULT '{}', worker_error text
);
INSERT INTO notification_preferences(owner,timezone) SELECT 'owner',timezone FROM workspace_settings ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS attention_pending (
 subject_type text NOT NULL, subject_id uuid NOT NULL, generation bigint NOT NULL DEFAULT 1,
 due_at timestamptz NOT NULL DEFAULT now(), failures integer NOT NULL DEFAULT 0,
 PRIMARY KEY(subject_type,subject_id)
);
CREATE INDEX IF NOT EXISTS attention_pending_due ON attention_pending(due_at,subject_id);
CREATE TABLE IF NOT EXISTS attention_items (
 id uuid PRIMARY KEY, subject_type text NOT NULL, subject_id uuid NOT NULL, purpose text NOT NULL,
 category text NOT NULL, fingerprint text NOT NULL, version integer NOT NULL DEFAULT 1,
 read_version integer NOT NULL DEFAULT 0, dismissed_version integer NOT NULL DEFAULT 0,
 active boolean NOT NULL, needs_owner boolean NOT NULL, title text NOT NULL, summary text NOT NULL,
 source_status text NOT NULL, refs jsonb NOT NULL DEFAULT '{}', expires_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 read_at timestamptz, dismissed_at timestamptz, pruned_at timestamptz,
 UNIQUE(subject_type,subject_id,purpose)
);
CREATE INDEX IF NOT EXISTS attention_owner_list ON attention_items(updated_at DESC,id);
CREATE TABLE IF NOT EXISTS push_subscriptions (
 id uuid PRIMARY KEY, device_hash text NOT NULL UNIQUE, endpoint_hash text NOT NULL UNIQUE,
 secret_encrypted text, generation uuid NOT NULL, origin text NOT NULL, key_id text NOT NULL,
 label text NOT NULL, enabled boolean NOT NULL DEFAULT true, status text NOT NULL DEFAULT 'registered',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '90 days'
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
 id uuid PRIMARY KEY, subscription_id uuid NOT NULL REFERENCES push_subscriptions ON DELETE CASCADE,
 generation uuid NOT NULL, purpose text NOT NULL, item_id uuid REFERENCES attention_items,
 item_version integer, policy_revision integer NOT NULL, batch_key text,
 status text NOT NULL DEFAULT 'pending', due_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 0,
 lease_token uuid, lease_until timestamptz, result text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(subscription_id,generation,purpose)
);
ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;
CREATE INDEX IF NOT EXISTS task_canonical_work ON tasks ((COALESCE(management_work_item_id,work_item_id)));
CREATE INDEX IF NOT EXISTS notification_recent_attempts ON notification_deliveries(subscription_id,last_attempt_at DESC) WHERE last_attempt_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS notification_due ON notification_deliveries(due_at) WHERE status IN ('pending','dispatching');
-- Triggers cover direct SQL closures too, in the same transaction as the source change.
CREATE OR REPLACE FUNCTION attention_dirty(kind text, identity uuid) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF identity IS NOT NULL THEN
  INSERT INTO attention_pending(subject_type,subject_id) VALUES(kind,identity)
  ON CONFLICT(subject_type,subject_id) DO UPDATE SET generation=attention_pending.generation+1,due_at=now(),failures=0;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION attention_source_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE value jsonb; kind text;
BEGIN
 value:=to_jsonb(NEW); kind:=TG_ARGV[0];
 IF kind='task' THEN PERFORM attention_dirty('task',NEW.task_id);
 ELSIF kind='work' THEN PERFORM attention_dirty('work',NEW.work_item_id);
 ELSIF kind='execution' THEN
  IF TG_OP='INSERT' OR (to_jsonb(OLD)->>'status',to_jsonb(OLD)->>'work_item_id',to_jsonb(OLD)->>'message_intent',to_jsonb(OLD)->>'grant_id')
     IS DISTINCT FROM (value->>'status',value->>'work_item_id',value->>'message_intent',value->>'grant_id') THEN
   PERFORM attention_dirty('task',NEW.id);
   IF TG_OP='UPDATE' THEN PERFORM attention_dirty('work',OLD.work_item_id); END IF;
  END IF;
 ELSIF kind='conversation' THEN
  IF TG_OP='UPDATE' AND OLD.archived_at IS DISTINCT FROM NEW.archived_at THEN
   PERFORM attention_dirty('task',id) FROM tasks WHERE conversation_id=NEW.id;
  END IF;
 ELSIF kind='grant' THEN PERFORM attention_dirty('task',id) FROM tasks WHERE grant_id=NEW.id AND status NOT IN ('completed','failed','cancelled','interrupted');
 ELSE PERFORM attention_dirty(CASE WHEN kind='work_item' THEN 'work' ELSE kind END,NEW.id);
 END IF;
 RETURN NEW;
END $$;
DO $$
DECLARE spec text[];
BEGIN
 FOREACH spec SLICE 1 IN ARRAY ARRAY[
 ['tasks','execution'],['input_requests','task'],['approvals','task'],['connector_operations','task'],
 ['work_outcomes','work'],['work_items','work_item'],['routine_definitions','routine'],
 ['event_trigger_definitions','trigger'],['event_sources','source'],['connections','connection'],
 ['conversations','conversation'],['grants','grant']] LOOP
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='attention_'||spec[1]) THEN
  EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION attention_source_changed(%L)', 'attention_'||spec[1],spec[1],spec[2]);
 END IF;
 END LOOP;
END $$;
-- One-time bootstrap; historical completions are materialized read and never pushed.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM schema_versions WHERE version=17) THEN
  PERFORM attention_dirty('task',id) FROM tasks WHERE status NOT IN ('completed','failed','cancelled','interrupted') OR updated_at>now()-interval '24 hours';
  PERFORM attention_dirty('work',id) FROM work_items WHERE hidden_at IS NULL;
  PERFORM attention_dirty('source',id) FROM event_sources WHERE enabled;
  PERFORM attention_dirty('connection',id) FROM connections;
  PERFORM attention_dirty('routine',id) FROM routine_definitions WHERE state<>'archived';
  PERFORM attention_dirty('trigger',id) FROM event_trigger_definitions WHERE state<>'archived';
 END IF;
END $$;
INSERT INTO schema_versions(version) VALUES(17) ON CONFLICT DO NOTHING;
