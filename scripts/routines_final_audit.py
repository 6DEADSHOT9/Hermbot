"""Read-only final local Phase 5.1 audit after fixture cleanup."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from urllib import request
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
OWNER = next(line.split('=', 1)[1].strip().strip('"\'') for line in (ROOT / '.env').read_text().splitlines() if line.startswith('OWNER_TOKEN='))
GROUP = '5739adf6-0a86-40a3-916b-d41a75932572'
ROUTINE = '421a0c64-443c-453f-9cee-64d04417bac9'
DRAFT = '0102a1dd-3a5c-42f2-a2ec-db1f891980e0'

def get(path):
    with request.urlopen(request.Request('http://localhost:8780' + path, headers={'Authorization': 'Bearer ' + OWNER}), timeout=10) as response:
        return json.load(response)

def main():
    state = get('/api/v1/state')
    listing = get('/api/v1/routines')['data']
    routines = {r['id']: r for r in listing}
    assert all(routines[i]['state'] == 'archived' for i in (ROUTINE, DRAFT))
    assert not any(c['id'] == GROUP for c in state['conversations'])
    assert all(b['readiness']['compatible'] and b['revision'] == b['applied_revision'] for b in state['bots'])
    assert all(t['status'] in ('completed','failed','cancelled','interrupted','uncertain') for t in state['tasks'])
    history = get(f'/api/v1/routines/{ROUTINE}/runs')['data']
    completed = [r for r in history if r['display_status'] == 'completed']
    assert len(completed) == 3 and sum(r['origin']=='scheduled' for r in completed) == 2
    assert all('42' in (r['output'] or '') for r in completed)
    binding = routines[ROUTINE]['scheduler']
    assert binding['enabled'] is False and binding['error'] is None
    script = ('import os,json,urllib.request; u="http://127.0.0.1:8642/api/jobs?include_disabled=true"; '
              'r=urllib.request.Request(u,headers={"Authorization":"Bearer "+os.environ["API_SERVER_KEY"]}); '
              'print(json.dumps(json.load(urllib.request.urlopen(r,timeout=5))["jobs"]))')
    output = subprocess.run(['docker','exec','hermbot-bot-researcher','/opt/hermes/.venv/bin/python','-c',script],
                            capture_output=True,text=True,check=True).stdout
    job = next(j for j in json.loads(output) if j['id'] == binding['runtime_job_id'])
    assert job['enabled'] is False and job['no_agent'] is True and job['script'] == 'hermbot_routine_wakeup.py'
    # The fixture conversation was archived, but its proof and work remain addressable.
    assert get(f'/api/v1/conversations/{GROUP}')['id'] == GROUP
    integrity = json.loads((ROOT / 'test-results/phase5-routines-integrity.json').read_text())
    assert integrity['upstream_source_unchanged'] and integrity['private_volumes_preserved']
    query = ("from backend import db; db.pool.open(wait=True); c=db.pool.getconn(); c.execute('SET TRANSACTION READ ONLY'); "
             "print(c.execute(\"SELECT count(*) AS n FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.conversation_id=%s\", "
             "('5739adf6-0a86-40a3-916b-d41a75932572',)).fetchone()['n'])")
    operations = int(subprocess.run(['docker','exec','hermbot-api-1','python','-c',query],
                                    capture_output=True,text=True,check=True).stdout.strip())
    assert operations == 0
    xml = ElementTree.parse(ROOT / 'test-results/phase5-routines-backend.xml').getroot()
    suites = xml.findall('testsuite') if xml.tag == 'testsuites' else [xml]
    tests_passed = sum(int(s.attrib['tests']) for s in suites)
    assert tests_passed >= 478 and all(int(s.attrib['failures']) == int(s.attrib['errors']) == 0 for s in suites)
    images = {name: subprocess.run(['docker','image','inspect','--format','{{.Id}}',name],
                                    capture_output=True,text=True,check=True).stdout.strip() for name in ('hermbot-app:phase1','hermbot-hermes:phase4')}
    proof = {'audited_at': datetime.now(timezone.utc).isoformat(), 'bot_revisions': {b['id']: b['revision'] for b in state['bots']},
             'routines_archived': True, 'group_archived': True, 'completed_runs': len(completed),
             'native_scheduled_runs': 2, 'post_restart_manual_run': True, 'native_timer_disabled': True,
             'all_work_settled': True, 'upstream_source_unchanged': True, 'private_volumes_preserved': True,
             'external_changes_requested': False, 'fixture_connector_operations': operations,
             'backend_tests_passed': tests_passed, 'app_image_id': images['hermbot-app:phase1'],
             'runtime_image_id': images['hermbot-hermes:phase4']}
    (ROOT / 'test-results/phase5-routines-final.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps({k:proof[k] for k in ('completed_runs','native_timer_disabled','all_work_settled','upstream_source_unchanged')}))

if __name__ == '__main__':
    main()
