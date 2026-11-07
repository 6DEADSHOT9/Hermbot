"""Actual public MCP discovery, with no bot assignment, secret or tools/call request."""
import json
from pathlib import Path
from uuid import uuid4

from .protocol_acceptance import client


def main():
    response = client.post('/api/v1/connections', headers={'Idempotency-Key': str(uuid4())}, json={'provider': 'mcp', 'name': 'Phase 4 public documentation MCP proof', 'endpoint': 'https://docs.mcp.cloudflare.com/mcp', 'bot_ids': [], 'actions': [], 'resources': {}})
    response.raise_for_status()
    connection = response.json()
    try:
        client.post('/api/v1/connections/' + connection['id'] + '/token', json={'token': '', 'expected_revision': connection['revision']}).raise_for_status()
        response = client.post('/api/v1/connections/' + connection['id'] + '/discover', timeout=90)
        if not response.is_success:
            raise AssertionError(response.json().get('detail', 'Discovery failed'))
        discovered = response.json()
        assert discovered['capabilities'] and discovered['actions'] == [] and discovered['bot_ids'] == []
        proof = {'actual_public_mcp': True, 'endpoint': discovered['endpoint'], 'discovered_tools': [t['name'] for t in discovered['capabilities']], 'tools_not_automatically_authorized': True, 'bot_assignment_count': 0, 'tool_calls': 0, 'google_slack_github_acceptance': False}
        Path('/app/test-results/phase4-mcp-discovery.json').write_text(json.dumps(proof, indent=2))
        print('Real public MCP discovery passed; no tools were called or granted to bots.', flush=True)
    finally:
        client.post('/api/v1/connections/' + connection['id'] + '/revoke').raise_for_status()


if __name__ == '__main__':
    main()
