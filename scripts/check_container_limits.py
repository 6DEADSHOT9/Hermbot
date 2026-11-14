"""Prove Docker resource controls in disposable containers, without workspace credentials."""
import json
from pathlib import Path
import subprocess
from uuid import uuid4


IMAGE = 'hermbot-tools:phase1'
PREFIX = 'hermbot-a1-proof-' + uuid4().hex[:12]


def docker(*args, timeout=40):
    return subprocess.run(['docker', *args], capture_output=True, text=True, check=True, timeout=timeout).stdout.strip()


def run():
    names = [PREFIX + suffix for suffix in ('-limits', '-logs', '-read')]
    common = ['--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
              '--memory', '128m', '--memory-swap', '128m', '--cpus', '.25', '--pids-limit', '128',
              '--oom-score-adj', '1000', '--log-driver', 'json-file', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=2']
    result = {}
    try:
        output = docker('run', '--rm', '--name', names[0], *common, '--user', '10001:10001',
                        '--ulimit', 'nofile=1024:16384', IMAGE, 'python', '-I', '-c', '''
import errno,json,os,resource,signal,time
from pathlib import Path
assert Path('/sys/fs/cgroup/pids.max').read_text().strip()=='128'
assert Path('/sys/fs/cgroup/memory.swap.max').read_text().strip()=='0'
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(1024,16384)
try: resource.setrlimit(resource.RLIMIT_NOFILE,(1024,16385))
except (ValueError,PermissionError): pass
else: raise AssertionError('Unprivileged process raised its hard file limit')
files=[]
try:
    for _ in range(1100):
        try: files.append(open('/dev/null'))
        except OSError as error:
            assert error.errno==errno.EMFILE
            break
    else: raise AssertionError('Open-file limit did not stop the bounded probe')
finally:
    for file in files: file.close()
children=[]
try:
    # Bounded even if the controller fails: never more than 150 attempts.
    for _ in range(150):
        try: pid=os.fork()
        except OSError as error:
            assert error.errno==errno.EAGAIN
            break
        if pid==0:
            time.sleep(15)
            os._exit(0)
        children.append(pid)
    else: raise AssertionError('Process limit did not stop the bounded probe')
finally:
    for pid in children:
        try: os.kill(pid,signal.SIGKILL)
        except ProcessLookupError: pass
    for pid in children: os.waitpid(pid,0)
print(json.dumps({'pids_max':128,'forked_children':len(children),'swap_bytes':0,
                  'nofile_soft':1024,'nofile_hard':16384,'opened_files':len(files)}))
''')
        result['kernel'] = json.loads(output)
        container = docker('create', '--name', names[1], *common, IMAGE, 'python', '-I', '-u', '-c',
                           "import sys; [sys.stdout.write('x'*16000+'\\n') for _ in range(256)]; sys.stdout.flush()")
        docker('start', container)
        assert docker('wait', container) == '0'
        info = json.loads(docker('inspect', container))[0]
        log_path = Path(info['LogPath'])
        assert log_path.parent.name == container and log_path.is_absolute()
        output = docker('run', '--rm', '--name', names[2], *common,
                        '--mount', f'type=bind,src={log_path.parent},dst=/logs,readonly', IMAGE, 'python', '-I', '-c',
                        "import json; from pathlib import Path; print(json.dumps([{'name':p.name,'size':p.stat().st_size} for p in Path('/logs').glob('*-json.log*')]))")
        files = json.loads(output)
        assert len(files) == 2 and any(row['name'].endswith('.1') for row in files), files
        assert all(row['size'] <= 1024 ** 2 + 20000 for row in files), files
        result['rotation'] = {'files': len(files), 'sizes': [row['size'] for row in files]}
        print(json.dumps(result, indent=2))
    finally:
        cleanup_errors = []
        for name in names:
            try:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_errors.append(name)
        if cleanup_errors:
            raise RuntimeError('Cleanup could not finish for owned test containers: ' + ', '.join(cleanup_errors))


if __name__ == '__main__':
    run()
