-- The runtime layout a bot was last reprovisioned for. Runtimes are recreated only when a bot's
-- revision changes, so registry bootstrap bumps it once whenever RUNTIME_LAYOUT rises.
ALTER TABLE bots ADD COLUMN IF NOT EXISTS runtime_layout integer NOT NULL DEFAULT 0;
