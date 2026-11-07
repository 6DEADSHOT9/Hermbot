"""Read-only local Phase 4 implementation audit; never substitutes for real provider acceptance."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent.parent
results = root / 'test-results'


def command(*args):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description='Audit local implementation and actual acceptance evidence without executing provider actions')
    parser.add_argument('--allow-pending-owner-reviews', action='store_true', help='Permit settled native connector drafts awaiting their human decision; still requires no running/queued/uncertain effects')
    args = parser.parse_args()
    token = next(line.split('=', 1)[1].strip().strip('\"\'') for line in (root / '.env').read_text().splitlines() if line.startswith('OWNER_TOKEN='))
    def get(path):
        request = urllib.request.Request('http://localhost:8780' + path, headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)
    state, connected = get('/api/v1/state'), get('/api/v1/connections')
    assert state['provider_configured'] and not state['protocol_fixture']
    assert (root / '.env').stat().st_mode & 0o777 == 0o600
    runtime_count = len(state['bots'])
    assert runtime_count >= 3 and {'researcher', 'writer', 'reviewer'}.issubset({b['id'] for b in state['bots']})
    assert all(b['status'] == 'active' and b['applied_revision'] == b['revision'] and b['readiness'].get('compatible') for b in state['bots'])
    waiting = [t for t in state['tasks'] if t['status'] not in {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}]
    pending_reviews = []
    if args.allow_pending_owner_reviews:
        for task in waiting:
            assert task['status'] == 'waiting_approval', 'Only settled owner reviews may remain during this audit'
            detail = get('/api/v1/tasks/' + task['id'])
            assert detail['executions'] and all(e['status'] == 'completed' for e in detail['executions'])
            operations = [op for op in connected['operations'] if op['task_id'] == task['id'] and op['status'] == 'pending']
            assert len(operations) == 1
            op = operations[0]
            approval = next(a for a in state['approvals'] if a['id'] == op['approval_id'])
            assert approval['kind'] == 'connector' and approval['status'] == 'pending' and approval['payload_hash'] == op['payload_hash']
            assert not op['result'] and not op['lease_until']
            pending_reviews.append({'task_id': task['id'], 'approval_id': approval['id'], 'action': op['action']})
    else:
        assert not waiting
    permitted = {r['task_id'] for r in pending_reviews}
    assert not any(op['status'] in {'queued', 'dispatching', 'uncertain'} or (op['status'] == 'pending' and op['task_id'] not in permitted) for op in connected['operations'])
    assert all('secret_encrypted' not in c for c in connected['connections'])
    suites = ET.parse(results / 'backend.xml').getroot().findall('testsuite')
    count = sum(int(s.attrib['tests']) for s in suites)
    assert count >= 222 and all(int(s.attrib['failures']) == int(s.attrib['errors']) == 0 for s in suites)
    mcp = json.loads((results / 'phase4-mcp-discovery.json').read_text())
    assert mcp['actual_public_mcp'] and mcp['tools_not_automatically_authorized'] and mcp['tool_calls'] == 0
    reviewed = json.loads((results / 'phase4-mcp-review.json').read_text())
    recovery = json.loads((results / 'phase4-mcp-response-loss.json').read_text())
    assert reviewed['actual_provider_receipt'] and reviewed['stale_approval_rejected'] and reviewed['definitive_receipt_continuation'] and reviewed['temporary_connection_detached_and_revoked'] and reviewed['script_errors'] == []
    assert recovery['closed_outcome'] == 'unresolved' and not recovery['original_search_replayed'] and not recovery['stopped_task_resumed'] and recovery['retry_before_and_after_closure'] == 409
    closed = json.loads((results / 'phase4-mcp-recovery-closed.json').read_text())
    assert closed['closed_unknown_outcome_remains_visible'] and closed['retry_disabled'] and closed['later_verified_reconciliation_available'] and closed['script_errors'] == []
    preflight = json.loads((results / 'phase4-private-preflight.json').read_text())
    assert preflight['runner_help_passed'] and preflight['unconfigured_read_and_review_runs_refused_before_submission'] and preflight['tasks_created'] == preflight['groups_created'] == preflight['external_effects'] == 0 and not preflight['private_provider_acceptance']
    native = json.loads((results / 'phase4-native.json').read_text())
    browser = json.loads((results / 'phase4-connections.json').read_text())
    regressions = json.loads((results / 'ui-regressions.json').read_text())
    assert native['real_native_model'] and native['native_policy_error_observed'] and native['connector_tool_access_denied_before_provider_call']
    assert not native['real_provider_acceptance']
    current_native = json.loads((results / 'native-tools-acceptance.json').read_text())
    isolation = json.loads((results / 'native-tools-audit.json').read_text())
    assert current_native['complete'] and current_native['real_native_model'] and current_native['fixture_archived']
    assert current_native['connected_helper_terminal_verified'] and current_native['parent_terminal_after_handoff_verified']
    assert not current_native['external_changes'] and isolation['running_source_matches_official_base']
    assert browser['script_errors'] == [] and browser['fixture_revoked']
    assert regressions['script_errors'] == []
    authorized = sorted({c['provider'] for c in connected['connections'] if c['status'] == 'connected'})
    private_reads = {}
    smoke_path = results / 'phase4-connected-smoke.json'
    if smoke_path.exists():
        smoke = json.loads(smoke_path.read_text())
        if smoke.get('real_private_provider_acceptance'):
            assert smoke['real_native_model'] and smoke['external_mutations'] == 0 and not smoke['full_phase4_exit_gate']
            private_reads['connected_smoke'] = smoke['verified']
    github_path = results / 'phase4-github-smoke.json'
    if github_path.exists():
        github = json.loads(github_path.read_text())
        if github.get('verified'):
            proof = github['verified']
            assert proof['real_private_provider_reads'] and proof['real_native_model'] and proof['action'] == 'github.repositories'
            assert proof['external_mutations'] == 0 and not proof['full_phase4_exit_gate']
            private_reads['github_repository_list'] = proof
    selected_path = results / 'phase4-google-slack-reads.json'
    selected_pending = False
    if selected_path.exists():
        selected = json.loads(selected_path.read_text())
        if selected.get('google_file_verified'):
            file_proof = selected['google_file_verified']
            assert file_proof['real_private_provider_reads'] and file_proof['real_native_model'] and file_proof['selected_csv_imported_and_read']
            assert file_proof['external_mutations'] == 0 and not file_proof['full_phase4_exit_gate']
            private_reads['selected_drive_csv'] = file_proof
        if selected.get('verified'):
            proof = selected['verified']
            assert proof['real_private_provider_reads'] and proof['real_native_model'] and proof['explicit_artifact_handoffs_verified']
            assert proof['external_mutations'] == 0 and proof['manager_without_app_access'] and not proof['full_phase4_exit_gate']
            private_reads['google_slack_orchestration'] = proof
        else:
            selected_pending = True
    full_reads_path = results / 'phase4-real-reads.json'
    if full_reads_path.exists():
        proof = json.loads(full_reads_path.read_text())
        manifest = json.loads((results / 'phase4-real-reads-manifest.json').read_text())
        assert manifest.get('verified') == proof and manifest['task_id'] == proof['task_id']
        assert proof['real_provider_reads'] and proof['real_native_model'] and proof['explicit_artifact_handoffs_verified']
        assert proof['external_mutations'] == 0 and proof['status'] == 'completed' and not proof['full_phase4_exit_gate']
        assert proof['providers'] == ['github', 'google', 'slack'] and len(proof['child_task_ids']) == 2
        assert len(proof['report_artifact_ids']) >= 3 and len(proof['imported_artifact_ids']) == 1
        assert {'gmail.search', 'gmail.read', 'calendar.availability', 'drive.search', 'drive.import', 'slack.search', 'slack.history', 'github.repository', 'github.issues', 'github.pulls'}.issubset(proof['verified_actions'])
        private_reads['three_provider_orchestration'] = proof
    failed_reads_path = results / 'phase4-real-reads-failed.json'
    failed_reads = []
    if failed_reads_path.exists():
        failed = json.loads(failed_reads_path.read_text())
        assert failed['real_native_model'] and failed['external_mutations'] == 0 and not failed['full_phase4_exit_gate']
        assert failed['verification_failed'] and any(not outcome['success'] for outcome in failed['connector_outcomes'])
        failed_reads.append(failed)
    remaining = [f'{p} account authorization or reconnect' for p in ('google', 'slack', 'github') if p not in authorized]
    if selected_pending:
        remaining.append('selected Slack channel history and Google/Slack artifact orchestration')
    if 'three_provider_orchestration' not in private_reads:
        remaining.append('full Google/Slack/GitHub read orchestration')
    remaining += ['owner-reviewed private-provider effects', 'final post-acceptance review']
    reviewed_effects = []
    for path in results.glob('phase4-reviewed-*-verified.json'):
        effect = json.loads(path.read_text())
        assert effect['real_native_model'] and effect['real_private_provider'] and not effect['full_phase4_exit_gate']
        task = get('/api/v1/tasks/' + effect['task_id'])
        operation = next(op for op in connected['operations'] if op['id'] == effect['operation_id'])
        assert task['status'] == 'completed' and operation['task_id'] == effect['task_id']
        assert operation['action'] == effect['action'] and operation['status'] == effect['outcome']
        assert effect['outcome'] in {'succeeded', 'denied'}
        assert bool(operation['result']) == effect['external_effect']
        reviewed_effects.append(effect)
    runtime_image = command('docker', 'image', 'inspect', 'hermbot-hermes:phase4', '--format', '{{.Id}}')
    containers = command('docker', 'ps', '-q', '--filter', 'label=com.hermbot.project=hermbot', '--filter', 'label=com.hermbot.kind=runtime').split()
    assert len(containers) == runtime_count
    template = '{"bot_id":{{json (index .Config.Labels "com.hermbot.bot-id")}},"image":{{json .Image}},"health":{{json .State.Health.Status}},"mounts":{{json .Mounts}},"memory":{{json .HostConfig.Memory}}}'
    mounts = []
    runtime_bots = set()
    for row in command('docker', 'inspect', '--format', template, *containers).splitlines():
        c = json.loads(row)
        runtime_bots.add(c['bot_id'])
        assert c['image'] == runtime_image and c['health'] == 'healthy' and c['memory'] > 0
        private_mounts = [m for m in c['mounts'] if m['Type'] == 'volume']
        assert {m['Destination'] for m in private_mounts} == {'/opt/data', '/workspace', '/browser-state'}
        assert all(m['Type'] in {'volume', 'tmpfs'} for m in c['mounts'])
        mounts.extend(m['Name'] for m in private_mounts)
    assert runtime_bots == {b['id'] for b in state['bots']}
    assert len(mounts) == len(set(mounts)) == 3 * runtime_count
    app_image = command('docker', 'image', 'inspect', 'hermbot-app:phase1', '--format', '{{.Id}}')
    for name in ('hermbot-api-1', 'hermbot-worker-1', 'hermbot-supervisor-1'):
        assert command('docker', 'inspect', name, '--format', '{{.Image}}') == app_image
    proof = {'audited_at': datetime.now(timezone.utc).isoformat(), 'implementation_checks_passed': True, 'backend_tests': count, 'healthy_runtime_count': runtime_count, 'isolated_private_volumes': len(mounts), 'runtime_image': runtime_image, 'app_image': app_image, 'historical_native_policy_verified': True, 'connected_native_tools_verified': True, 'unmodified_hermes_verified': True, 'real_public_mcp_review_verified': True, 'lost_response_recovery_verified': True, 'authorized_private_providers': authorized, 'pending_owner_connector_reviews': pending_reviews, 'private_read_evidence': private_reads, 'real_provider_exit_gate': False, 'remaining': remaining, 'acceptance_source_hashes': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'tests').glob('*phase4*.py')}, 'source_hashes': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('backend', 'plugins', 'web/src') for p in (root / folder).rglob('*') if p.is_file() and p.suffix in {'.py', '.sql', '.tsx', '.ts', '.css', '.yaml'}}}
    proof.update(healthy_runtime_count=runtime_count,
                 isolated_private_volumes=len(mounts), failed_private_read_attempts=failed_reads,
                 reviewed_private_effects=reviewed_effects)
    (results / 'phase4-implementation-audit.json').write_text(json.dumps(proof, indent=2))
    print(f'Phase 4 implementation audit passed: {count} backend tests, {runtime_count} healthy runtimes, isolated storage and current native tool access. Real provider exit gate pending.')


if __name__ == '__main__':
    main()
