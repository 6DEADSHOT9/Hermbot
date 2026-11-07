"""Encrypted local account records; a separate OS-held key, no application DB secrets."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat

from cryptography.fernet import Fernet, MultiFernet


def private_directory(directory):
    directory = Path(directory).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Broker directory must be an owner-only directory')
    return directory


def private_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        os.close(fd)
        raise RuntimeError('Broker file must be an owner-only regular file')
    return fd


def os_keys(create=False):
    # Select the OS backend explicitly: no plugin discovery or plaintext fallback.
    from keyring.backends.macOS import Keyring
    store = Keyring()
    key = store.get_password('com.hermbot.model-broker', 'vault-v1')
    if key is None and create:
        key = Fernet.generate_key().decode()
        store.set_password('com.hermbot.model-broker', 'vault-v1', key)
    if not key:
        raise RuntimeError('Unlock the existing Hermbot Keychain item; no replacement key was created')
    keys = json.loads(key)['keys'] if key.startswith('{') else [key]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8 or any(not isinstance(value, str) for value in keys):
        raise RuntimeError('Invalid broker Keychain record; existing state was preserved')
    for value in keys:
        Fernet(value.encode())
    return [value.encode() for value in keys]


def save_os_keys(keys):
    from keyring.backends.macOS import Keyring
    Keyring().set_password('com.hermbot.model-broker', 'vault-v1', json.dumps({'version': 1, 'keys': [key.decode() for key in keys]}))


class Vault:
    def __init__(self, directory, key=None):
        self.directory = private_directory(directory)
        self.lock_fd = private_file(self.directory / 'vault.lock')
        with self.lock():
            filename = self.directory / 'accounts.sqlite3'
            exists = filename.exists()
            fd = private_file(filename)
            os.close(fd)
            self.keys = [key] if key is not None else os_keys(create=not exists or filename.stat().st_size == 0)
            self.cipher = MultiFernet([Fernet(value) for value in self.keys])
            self.conn = sqlite3.connect(filename, isolation_level=None)
            self.conn.execute('PRAGMA synchronous=FULL')
            self.conn.execute('PRAGMA journal_mode=DELETE')
            self.conn.execute('CREATE TABLE IF NOT EXISTS state (id TEXT PRIMARY KEY, encrypted BLOB NOT NULL)')
            # Reading verifies the key before any subsequent writes can replace existing state.
            for row in self.conn.execute('SELECT encrypted FROM state'):
                json.loads(self.cipher.decrypt(row[0]))

    @contextmanager
    def lock(self):
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)

    def get(self, record_id):
        row = self.conn.execute('SELECT encrypted FROM state WHERE id=?', (record_id,)).fetchone()
        return json.loads(self.cipher.decrypt(row[0])) if row else None

    def put(self, record_id, value):
        ciphertext = self.cipher.encrypt(json.dumps(value).encode())
        self.conn.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET encrypted=excluded.encrypted', (record_id, ciphertext))

    def close(self):
        self.conn.close()
        os.close(self.lock_fd)

    def rotate_key(self, save_keys=save_os_keys):
        with self.lock():
            if len(self.keys) >= 8:
                raise RuntimeError('Finish recovering the existing Keychain rotation first')
            records = [(row[0], self.cipher.decrypt(row[1])) for row in self.conn.execute('SELECT id,encrypted FROM state')]
            new = Fernet.generate_key()
            successor = Fernet(new)
            # Retain every decrypting key before changing ciphertext; crashes remain recoverable.
            save_keys([new, *self.keys])
            try:
                self.conn.execute('BEGIN IMMEDIATE')
                for record_id, plaintext in records:
                    self.conn.execute('UPDATE state SET encrypted=? WHERE id=?', (successor.encrypt(plaintext), record_id))
                self.conn.execute('COMMIT')
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute('ROLLBACK')
                raise
            self.keys, self.cipher = [new], MultiFernet([successor])
            for row in self.conn.execute('SELECT encrypted FROM state'):
                successor.decrypt(row[0])
            save_keys([new])
