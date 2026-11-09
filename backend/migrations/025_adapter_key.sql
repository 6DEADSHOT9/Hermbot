-- The private adapter gets its own credential instead of sharing the Hermes API key.
-- adapter_key_revision is the bot revision whose runtime first receives that credential.
ALTER TABLE bots ADD COLUMN IF NOT EXISTS adapter_key_encrypted text;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS adapter_key_revision integer NOT NULL DEFAULT 0;
