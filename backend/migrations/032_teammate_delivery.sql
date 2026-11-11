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
