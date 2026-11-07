"""Readable provider names preserve owner edits, uniqueness and model configuration."""
# ruff: noqa: F811 -- imported pytest fixtures are intentionally used as test parameters.
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

from backend.settings import settings

from backend import db, model_auth, registry
from model_broker.codex import BASE_URL, CLIENT_ID, CodexAccounts
from tests.test_model_auth import codex_credentials, codex_network, issuer as issuer, owner as owner, vault as vault
from tests.test_codex_auth import connected


def test_new_connections_have_readable_unique_pending_names(owner):
    first = owner.post('/api/v1/providers/codex').json()
    second = owner.post('/api/v1/providers/codex').json()
    assert first['name'] == 'Codex connection'
    assert second['name'] == 'Codex connection (2)'
    assert first['id'][:8] not in first['name']


def test_verified_label_names_connection_and_unrelated_save_keeps_automatic(owner, monkeypatch):
    row = connected(owner)
    label = 'first@example.invalid'
    async def call(*args, **kwargs):
        return {'status': 'connected', 'plan_enabled': True, 'label': label, 'plan': 'Pro'}
    monkeypatch.setattr(model_auth.relay, 'call', call)
    assert owner.post(f"/api/v1/providers/{row['id']}/chatgpt/status").status_code == 200
    current = model_auth.provider(row['id'])
    assert current['name'] == 'Codex · first@example.invalid'
    assert current['account_plan'] == 'Pro'
    assert current['revision'] == row['revision'] and current['default_model'] == row['default_model']
    body = {'auth_kind': 'codex_oauth', 'name': current['name'], 'base_url': BASE_URL, 'models': current['models'], 'default_model': current['default_model'], 'expected_revision': current['revision']}
    response = owner.put(f"/api/v1/providers/{row['id']}", json=body, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 200
    assert not model_auth.provider(row['id'])['name_is_custom']
    label = 'second@example.invalid'
    owner.post(f"/api/v1/providers/{row['id']}/chatgpt/status")
    assert model_auth.provider(row['id'])['name'] == 'Codex · second@example.invalid'


def test_explicit_and_concurrent_custom_names_survive_status(owner, monkeypatch):
    row = connected(owner)
    body = {'auth_kind': 'codex_oauth', 'name': 'Work Codex', 'base_url': BASE_URL, 'models': row['models'], 'default_model': row['default_model'], 'expected_revision': row['revision']}
    assert owner.put(f"/api/v1/providers/{row['id']}", json=body, headers={'Idempotency-Key': str(uuid4())}).status_code == 200
    assert model_auth.provider(row['id'])['name_is_custom']
    async def call(*args, **kwargs):
        with db.connection() as conn:
            conn.execute("UPDATE providers SET name='Owner renamed again' WHERE id=%s", (row['id'],))
        return {'status': 'connected', 'plan_enabled': True, 'label': 'verified@example.invalid'}
    monkeypatch.setattr(model_auth.relay, 'call', call)
    owner.post(f"/api/v1/providers/{row['id']}/chatgpt/status")
    assert model_auth.provider(row['id'])['name'] == 'Owner renamed again'


def test_existing_uuid_names_migrate_once_without_touching_custom_or_api(owner):
    row = connected(owner)
    custom = owner.post('/api/v1/providers/codex').json()
    with psycopg.connect(settings.database_url, prepare_threshold=None, row_factory=dict_row) as conn:
        with conn.transaction(force_rollback=True):
            conn.execute('ALTER TABLE providers DROP COLUMN name_is_custom')
            conn.execute('UPDATE providers SET name=%s,account_label=%s WHERE id=%s', ('Codex ' + row['id'].hex[:8], 'verified@example.invalid', row['id']))
            conn.execute("UPDATE providers SET name='My custom Codex' WHERE id=%s", (custom['id'],))
            api_before = conn.execute("SELECT id,name FROM providers WHERE auth_kind='api_key' ORDER BY id").fetchall()
            migration = Path('backend/migrations/022_provider_names.sql').read_text()
            conn.execute(migration)
            registry.bootstrap(conn)
            migrated = conn.execute('SELECT * FROM providers WHERE id=%s', (row['id'],)).fetchone()
            assert migrated['name'] == 'Codex · verified@example.invalid' and not migrated['name_is_custom']
            assert conn.execute('SELECT name_is_custom FROM providers WHERE id=%s', (custom['id'],)).fetchone()['name_is_custom']
            conn.execute('UPDATE providers SET name=%s,name_is_custom=true WHERE id=%s', ('Codex ' + row['id'].hex[:8], row['id']))
            conn.execute(migration)
            registry.bootstrap(conn)
            assert conn.execute('SELECT name FROM providers WHERE id=%s', (row['id'],)).fetchone()['name'] == 'Codex ' + row['id'].hex[:8]
            assert conn.execute("SELECT id,name FROM providers WHERE auth_kind='api_key' ORDER BY id").fetchall() == api_before

def test_name_collisions_are_bounded_and_safe_under_concurrent_creation():
    label = 'a' * 90 + '@example.invalid'
    def create(_):
        with db.connection() as conn:
            name = registry.provider_name(conn, 'codex_oauth', label)
            conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,auth_kind,transport,name_is_custom) VALUES(%s,%s,%s,'[]','','codex_oauth','responses',false)", (uuid4(), name, BASE_URL))
            return name
    with ThreadPoolExecutor(max_workers=4) as pool:
        names = list(pool.map(create, range(4)))
    assert len(set(names)) == 4 and all(len(name) <= 80 for name in names)
    assert any(name.endswith(' (2)') for name in names)


def test_codex_plan_label_comes_from_verified_identity(vault, issuer):
    async def run():
        accounts = CodexAccounts(vault)
        await accounts.http.aclose()
        accounts.http = httpx.AsyncClient(transport=codex_network(issuer, lambda r: None))
        result = codex_credentials(issuer)
        result['id_token'] = issuer[1](aud=CLIENT_ID, **{'https://api.openai.com/auth': {'chatgpt_account_id': 'fixture', 'chatgpt_plan_type': 'pro'}})
        cid = str(uuid4())
        await accounts.accept(cid, {}, result, 0)
        assert accounts.status(cid)['plan'] == 'Pro'
        await accounts.http.aclose()
    asyncio.run(run())
