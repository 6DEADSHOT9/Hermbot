-- A takeover pauses the bot's container. While `paused_at` is set nothing calls the bot, and its
-- computer status is served from `paused_state`. `finishing_until` and `skip_wait` let a reply in
-- progress finish first; `pause_failed` and `unpaused` record a container that could not be paused.
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS paused_at timestamptz;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS paused_state jsonb;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS unpaused boolean NOT NULL DEFAULT false;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS pause_failed boolean NOT NULL DEFAULT false;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS finishing_until timestamptz;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS skip_wait boolean NOT NULL DEFAULT false;
