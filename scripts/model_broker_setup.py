"""Install/remove this owner's local broker LaunchAgent; never read or print account tokens."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile

LABEL = 'com.hermbot.model-broker'


def save_pairing_path(root, directory):
    path = root / '.env'
    if path.is_symlink():
        raise RuntimeError('The private environment file must not be a symbolic link')
    text = path.read_text()
    lines = [line for line in text.splitlines() if not line.startswith('MODEL_BROKER_PAIRING_FILE=')]
    value = str(directory / 'pairing.sha256')
    if "'" in value or '\n' in value:
        raise RuntimeError('Unsupported private directory path')
    lines.append("MODEL_BROKER_PAIRING_FILE='" + value + "'")
    fd, temporary = tempfile.mkstemp(prefix='.env.broker-', dir=root)
    try:
        with os.fdopen(fd, 'w') as file:
            file.write('\n'.join(lines) + '\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    interpreter = root / '.model-broker-venv/bin/python'
    entry = root / 'model_broker/launch.py'
    target = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    domain = f'gui/{os.getuid()}'
    if target.is_symlink():
        raise RuntimeError('The LaunchAgent file must not be a symbolic link')
    if args.uninstall:
        if target.exists():
            saved = plistlib.loads(target.read_bytes())
            if saved.get('ProgramArguments') != [str(interpreter), '-I', str(entry)]:
                raise RuntimeError('The existing LaunchAgent belongs to another installation')
            subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=False, capture_output=True)
            target.unlink()
        print('Broker startup removed. Saved accounts remain in their private local vault.')
        return
    if not interpreter.exists():
        raise RuntimeError('Create the locked .model-broker-venv environment first')
    if target.exists():
        previous = plistlib.loads(target.read_bytes())
        if previous.get('ProgramArguments') != [str(interpreter), '-I', str(entry)]:
            raise RuntimeError('The existing LaunchAgent belongs to another installation')
        # Stop our existing singleton before preparation takes its process lock.
        subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], check=False, capture_output=True)
    subprocess.run([str(interpreter), '-I', str(entry), '--prepare'], check=True)
    directory = Path.home() / 'Library/Application Support/Hermbot/Model Auth'
    save_pairing_path(root, directory)
    value = {'Label': LABEL, 'ProgramArguments': [str(interpreter), '-I', str(entry)], 'RunAtLoad': True,
             'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 30,
             'WorkingDirectory': str(root), 'Umask': 0o077,
             'StandardOutPath': str(directory / 'broker.log'), 'StandardErrorPath': str(directory / 'broker-error.log')}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        previous = plistlib.loads(target.read_bytes())
        if previous.get('ProgramArguments') != value['ProgramArguments']:
            raise RuntimeError('The existing LaunchAgent belongs to another installation')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(plistlib.dumps(value))
        file.truncate()
        file.flush()
        os.fchmod(file.fileno(), 0o600)
        os.fsync(file.fileno())
    subprocess.run(['launchctl', 'bootstrap', domain, str(target)], check=True)
    print('Local broker startup installed. Open Providers in Hermbot to connect an account.')


if __name__ == '__main__':
    main()
