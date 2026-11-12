-- The scheduler looks at the messages still in play on every dispatch pass (about once a second), and the waiter
-- and the "already answered" checks look replies up by the message they answer. Index only those rows, as job
-- queues index their live rows, so the cost doesn't grow with finished history.
CREATE INDEX IF NOT EXISTS teammate_messages_live ON teammate_messages(created_at) WHERE status IN ('pending','delivered');
CREATE INDEX IF NOT EXISTS teammate_messages_reply_to ON teammate_messages(reply_to) WHERE reply_to IS NOT NULL;
