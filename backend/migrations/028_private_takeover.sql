-- A takeover's private owner session: where the API reaches it, and (encrypted) its token and the
-- cookies it started from, so cookies the owner removed are removed from the bot at handback.
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS session_url text;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS session_token_encrypted text;
ALTER TABLE computer_controls ADD COLUMN IF NOT EXISTS session_seed_encrypted text;
