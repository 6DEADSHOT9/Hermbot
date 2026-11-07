"""Verify unchanged upstream source, runtime protection and preserved owner state."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
BASE = 'nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db'
SOURCE = '''import hashlib,json,os
from pathlib import Path
root=Path('/opt/hermes'); files={}
for directory, children, names in os.walk(root):
    children[:]=[d for d in children if d not in ('.venv','.git','node_modules','__pycache__')]
    for name in names:
        path=Path(directory)/name
        if path.suffix=='.py': files[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(files,sort_keys=True))'''
PROTECTION = '''import json,os,tempfile
from pathlib import Path
plugin=Path('/opt/data/plugins/hermbot')
gateway_uids=[]
for process in Path('/proc').iterdir():
    if not process.name.isdigit(): continue
    try:
        if b'\\x00gateway\\x00run' in (process/'cmdline').read_bytes():
            gateway_uids.append(int(next(line.split()[1] for line in (process/'status').read_text().splitlines() if line.startswith('Uid:'))))
    except (PermissionError,FileNotFoundError,ProcessLookupError): pass
with tempfile.NamedTemporaryFile(dir='/workspace',prefix='.hermbot-native-probe-',mode='w+') as marker:
    marker.write('writable'); marker.seek(0); assert marker.read()=='writable'
print(json.dumps({'uid':os.getuid(),'gateway_uids':gateway_uids,'source_read_only':bool(os.statvfs('/opt/hermes').f_flag & os.ST_RDONLY),
'plugin_directory_writable':os.access(plugin,os.W_OK),'plugin_file_writable':os.access(plugin/'__init__.py',os.W_OK),
'workspace_writable':True,'docker_socket_present':Path('/var/run/docker.sock').exists(),
'effective_capabilities':next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')),
'no_new_privileges':next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('NoNewPrivs:'))}))'''


def command(*args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()


def main():
    token = next(line.split('=', 1)[1].strip().strip('\"\'') for line in (ROOT / '.env').read_text().splitlines() if line.startswith('OWNER_TOKEN='))
    def get(path):
        request = urllib.request.Request('http://localhost:8780' + path, headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    original = json.loads(command('docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                                 '/opt/hermes/.venv/bin/python', BASE, '-c', SOURCE))
    built = json.loads(command('docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                              '/opt/hermes/.venv/bin/python', 'hermbot-hermes:phase4', '-c', SOURCE))
    assert len(original) > 500 and built == original, 'Runtime source differs from the official pinned image.'
    state = get('/api/v1/state')
    proof = json.loads((ROOT / 'test-results/native-tools-acceptance.json').read_text())
    before = json.loads((ROOT / 'test-results/native-tools-before.json').read_text())
    assert proof['complete'] and proof['fixture_archived'] and not proof['external_changes']
    groups = {c['id'] for c in state['conversations'] if c['kind'] == 'group'}
    assert groups == set(proof['original_groups'])
    assert proof['original_groups'] == before['original_groups']
    assert proof['original_bots'] == before['bots'] and proof['original_connections'] == before['connections']
    assert proof['original_bots'] == {b['id']: {key: b[key] for key in ('enabled_tools', 'enabled_skills', 'memory_mb', 'cpus')} for b in state['bots']}
    assert proof['original_connections'] == [{key: c[key] for key in ('id', 'status', 'revision', 'bot_ids', 'actions', 'resources')} for c in get('/api/v1/connections')['connections']]
    volumes, runtimes = [], []
    for bot in state['bots']:
        assert bot['readiness']['compatible'] and bot['revision'] == bot['applied_revision']
        view = get('/api/v1/bots/' + bot['id'] + '/capabilities')
        assert not view['connector_restricted']
        if 'terminal' in bot['enabled_tools']:
            assert 'terminal' in view['native_tools']
        name = 'hermbot-bot-' + bot['id']
        template = '{"readonly":{{json .HostConfig.ReadonlyRootfs}},"security":{{json .HostConfig.SecurityOpt}},"health":{{json .State.Health.Status}},"mounts":{{json .Mounts}}}'
        detail = json.loads(command('docker', 'inspect', '--format', template, name))
        assert detail['readonly'] and 'no-new-privileges:true' in detail['security'] and detail['health'] == 'healthy'
        private = [m for m in detail['mounts'] if m['Type'] == 'volume']
        assert {m['Destination'] for m in private} == {'/opt/data', '/workspace', '/browser-state'}
        assert {m['Destination']: m['Name'] for m in private} == {m['Destination']: m['Name'] for m in before['volumes'][bot['id']] if m['Type'] == 'volume'}
        volumes.extend(m['Name'] for m in private)
        assert json.loads(command('docker', 'exec', name, '/opt/hermes/.venv/bin/python', '-c', SOURCE)) == original
        protection = json.loads(command('docker', 'exec', '--user', 'hermes', name,
                                        '/opt/hermes/.venv/bin/python', '-c', PROTECTION))
        assert protection['uid'] != 0 and protection['source_read_only'] and protection['workspace_writable']
        assert protection['gateway_uids'] and all(uid == protection['uid'] for uid in protection['gateway_uids'])
        assert not protection['plugin_directory_writable'] and not protection['plugin_file_writable'] and not protection['docker_socket_present']
        assert int(protection['effective_capabilities'], 16) == 0 and protection['no_new_privileges'] == '1'
        runtimes.append({'bot_id': bot['id'], **protection})
    assert len(volumes) == len(set(volumes)) == len(state['bots']) * 3
    suites = ET.parse(ROOT / 'test-results/backend.xml').getroot().findall('testsuite')
    assert all(int(s.attrib['failures']) == int(s.attrib['errors']) == 0 for s in suites)
    output = {'complete': True, 'audited_at': datetime.now(timezone.utc).isoformat(),
              'backend_tests': sum(int(s.attrib['tests']) for s in suites),
              'official_base': BASE, 'unchanged_upstream_python_files': len(original),
              'running_source_matches_official_base': True, 'runtimes': runtimes,
              'isolated_private_volumes': len(volumes), 'owner_configuration_preserved': True,
              'test_group_archived': True, 'external_changes': False}
    (ROOT / 'test-results/native-tools-audit.json').write_text(json.dumps(output, indent=2))
    print(f"Native tool audit passed: {len(original)} unchanged upstream files, {len(runtimes)} protected runtimes, {len(volumes)} preserved volumes.")


if __name__ == '__main__':
    main()
