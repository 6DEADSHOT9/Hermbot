-- Each Resend starts a new delivery of the same message. The revision tells deliveries apart, so an owner action
-- from an older view (another browser tab) can't resolve a newer failure: the action carries the revision it saw,
-- and a mismatch is refused (as Kubernetes resourceVersion and HTTP If-Match work). Code from before this
-- migration ignores the column.
ALTER TABLE teammate_messages ADD COLUMN IF NOT EXISTS delivery_revision integer NOT NULL DEFAULT 1;
