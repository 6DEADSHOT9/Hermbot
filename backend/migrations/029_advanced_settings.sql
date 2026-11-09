-- Advanced settings, revealed per browser in Settings. What a takeover does when the bot cannot be
-- paused, and how long it lets an in-progress reply finish first. Defaults: ask, pause immediately.
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS takeover_unpausable text NOT NULL DEFAULT 'ask'
    CHECK (takeover_unpausable IN ('ask', 'refuse', 'continue'));
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS takeover_reply_wait_seconds integer NOT NULL DEFAULT 0
    CHECK (takeover_reply_wait_seconds IN (0, 15, 30, 60));
