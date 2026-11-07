-- initialize() replays migrations; 020 must also accept existing Codex rows.
ALTER TABLE providers DROP CONSTRAINT IF EXISTS providers_auth_kind_check;
ALTER TABLE providers ADD CONSTRAINT providers_auth_kind_check CHECK(auth_kind IN ('api_key','chatgpt_oauth','codex_oauth'));
