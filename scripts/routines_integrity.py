"""Read-only source and runtime integrity proof for the Phase 5.1 local build."""
from datetime import datetime, timezone
import json
from native_tools_audit import BASE, PROTECTION, ROOT, SOURCE, command


def main(output_name='phase5-routines-integrity.json'):
    original = json.loads(command('docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                                  '/opt/hermes/.venv/bin/python', BASE, '-c', SOURCE))
    built = json.loads(command('docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                               '/opt/hermes/.venv/bin/python', 'hermbot-hermes:phase4', '-c', SOURCE))
    assert len(original) > 500 and original == built
    bots = []
    volumes = []
    for identity in ('researcher', 'reviewer', 'writer'):
        name = 'hermbot-bot-' + identity
        assert json.loads(command('docker', 'exec', name, '/opt/hermes/.venv/bin/python', '-c', SOURCE)) == original
        protection = json.loads(command('docker', 'exec', '--user', 'hermes', name,
                                        '/opt/hermes/.venv/bin/python', '-c', PROTECTION))
        assert protection['uid'] != 0 and protection['source_read_only'] and protection['workspace_writable']
        assert protection['gateway_uids'] and all(uid == protection['uid'] for uid in protection['gateway_uids'])
        assert not protection['plugin_directory_writable'] and not protection['plugin_file_writable']
        assert not protection['docker_socket_present'] and int(protection['effective_capabilities'], 16) == 0
        assert protection['no_new_privileges'] == '1'
        view = json.loads(command('docker', 'inspect', '--format', '{{json .Mounts}}', name))
        private = [m for m in view if m['Type'] == 'volume']
        assert {m['Destination'] for m in private} == {'/opt/data', '/workspace', '/browser-state'}
        volumes += [m['Name'] for m in private]
        bots.append({'id': identity, 'source_matches': True, 'protected': True,
                     'volumes': {m['Destination']: m['Name'] for m in private}})
    assert len(volumes) == len(set(volumes)) == 9
    old = json.loads((ROOT / 'test-results/native-tools-before.json').read_text())
    if 'volumes' in old:
        for bot in bots:
            assert bot['volumes'] == {m['Destination']: m['Name'] for m in old['volumes'][bot['id']] if m['Type'] == 'volume'}
    proof = {'checked_at': datetime.now(timezone.utc).isoformat(), 'upstream_python_files': len(original),
             'upstream_source_unchanged': True, 'bots': bots, 'private_volumes_preserved': True}
    (ROOT / 'test-results' / output_name).write_text(json.dumps(proof, indent=2))
    print(json.dumps({'upstream_python_files': len(original), 'bots': [b['id'] for b in bots], 'private_volumes_preserved': True}))


if __name__ == '__main__':
    main()
