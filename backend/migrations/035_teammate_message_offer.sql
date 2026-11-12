-- Which attempt a pending message was last shown to, and at which delivery revision. An acknowledgement is recorded
-- only for that offer, so one that arrives after a Resend (a new revision) or from an attempt that was shown an
-- earlier delivery records nothing. Nullable: code from before this migration ignores both columns.
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS offered_attempt_id uuid;
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS offered_revision integer;
