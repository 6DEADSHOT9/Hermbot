-- Repeated driver failures are counted per item so one bad attempt or operation is
-- quarantined instead of crashing the worker for every bot.
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS drive_failures integer NOT NULL DEFAULT 0;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS lease_takeovers integer NOT NULL DEFAULT 0;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS drive_started_at timestamptz;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS drive_not_before timestamptz;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS last_drive_error text;
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS last_drive_error_at timestamptz;
ALTER TABLE bot_operations ADD COLUMN IF NOT EXISTS drive_failures integer NOT NULL DEFAULT 0;
ALTER TABLE bot_operations ADD COLUMN IF NOT EXISTS drive_not_before timestamptz;
ALTER TABLE bot_operations ADD COLUMN IF NOT EXISTS last_drive_error text;
