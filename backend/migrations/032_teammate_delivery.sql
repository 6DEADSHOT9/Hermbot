-- A teammate message that can't be scheduled backs off and keeps its last error. After a few failures,
-- or at once when its recipient is gone, it is released: cancelled, with the reason, so a waiting sender
-- is told why. Every column is nullable or defaulted, so code from before this migration ignores them.
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS last_error text;
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS last_error_at timestamptz;
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS not_before timestamptz;
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS reason text;
-- 'bounced' (the recipient was deleted or archived), 'dropped' (its sending task or conversation can no longer
-- use an answer) or 'failed' (it kept failing and was given up).
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS released text;
-- The owner's answer to a given-up message: 'resend' (back in the queue) or 'done' (the owner handles it).
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS owner_action text;
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS owner_action_at timestamptz;
-- A cancelled message always says why, enforced in the database so no writer (a later bulk UPDATE, raw SQL)
-- can drop one silently. Messages cancelled before reasons existed are marked as such, then the rule is checked
-- for every row. Each step is skipped once done, so later service starts take no lock here.
-- Rolling back to code from before this change needs: ALTER TABLE teammate_messages DROP CONSTRAINT teammate_message_release_reason;
UPDATE teammate_messages SET released=COALESCE(released,'dropped'),reason=COALESCE(reason,'Cancelled before reasons were recorded')
 WHERE status='cancelled' AND (released IS NULL OR reason IS NULL);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='teammate_message_release_reason') THEN
  ALTER TABLE teammate_messages ADD CONSTRAINT teammate_message_release_reason
   CHECK (status <> 'cancelled' OR (released IS NOT NULL AND reason IS NOT NULL)) NOT VALID;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='teammate_message_release_reason' AND NOT convalidated) THEN
  ALTER TABLE teammate_messages VALIDATE CONSTRAINT teammate_message_release_reason;
 END IF;
END $$;
