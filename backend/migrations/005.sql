CREATE TABLE IF NOT EXISTS connector_apps (
    provider text PRIMARY KEY CHECK(provider IN ('google','slack','github')),
    client_id text NOT NULL,
    secret_encrypted text NOT NULL,
    revision integer NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS connections (
    id uuid PRIMARY KEY,
    provider text NOT NULL CHECK(provider IN ('google','slack','github','mcp')),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'disconnected',
    revision integer NOT NULL DEFAULT 1,
    secret_encrypted text,
    bot_ids jsonb NOT NULL DEFAULT '[]',
    actions jsonb NOT NULL DEFAULT '[]',
    resources jsonb NOT NULL DEFAULT '{}',
    endpoint text,
    account text,
    capabilities jsonb NOT NULL DEFAULT '[]',
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS connector_oauth_states (
    state_hash text PRIMARY KEY,
    binding_hash text NOT NULL,
    owner_hash text NOT NULL,
    connection_id uuid NOT NULL REFERENCES connections ON DELETE CASCADE,
    connection_revision integer NOT NULL,
    app_revision integer NOT NULL,
    verifier_encrypted text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS connector_permissions (
    id uuid PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES connections ON DELETE CASCADE,
    bot_id text NOT NULL REFERENCES bots,
    action text NOT NULL,
    resource text NOT NULL,
    expires_at timestamptz NOT NULL,
    max_uses integer NOT NULL CHECK(max_uses BETWEEN 1 AND 100),
    used integer NOT NULL DEFAULT 0,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS connector_operations (
    id uuid PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES connections,
    connection_revision integer NOT NULL,
    task_id uuid NOT NULL REFERENCES tasks,
    bot_id text NOT NULL REFERENCES bots,
    approval_id uuid UNIQUE REFERENCES approvals,
    permission_id uuid REFERENCES connector_permissions,
    action text NOT NULL,
    arguments jsonb NOT NULL,
    payload_hash text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    result jsonb,
    error text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS connector_operations_dispatch ON connector_operations(status,created_at);
INSERT INTO schema_versions(version) VALUES(5) ON CONFLICT DO NOTHING;
