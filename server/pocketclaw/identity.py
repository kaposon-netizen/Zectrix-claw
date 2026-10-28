"""Independent local identity storage; route authorization must be integrated separately."""
from __future__ import annotations
import hashlib
import hmac
import os
import threading
from functools import wraps
import secrets
import sqlite3
import time
from pathlib import Path


def _synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class IdentityStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS families (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password_hash BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS children (id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (token_hash BLOB PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), child_id TEXT NOT NULL REFERENCES children(id), token_hash BLOB NOT NULL UNIQUE);
        ''')
        self.db.commit()
        if path != ':memory:':
            Path(path).chmod(0o600)

    @staticmethod
    def _password(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    @_synchronized
    def create_family(self, name: str, password: str, child_name: str) -> tuple[str, str]:
        name, child_name = name.strip(), child_name.strip()
        if not name or not child_name or len(password) < 12 or len(password.encode()) > 1024:
            raise ValueError('家庭名、孩子名或密码无效')
        family_id, child_id = 'fam_' + secrets.token_hex(16), 'child_' + secrets.token_hex(16)
        salt = secrets.token_bytes(16)
        password_hash = self._password(password, salt)
        with self.db:
            self.db.execute('INSERT INTO families VALUES (?, ?, ?, ?)', (family_id, name, salt, password_hash))
            self.db.execute('INSERT INTO children VALUES (?, ?, ?)', (child_id, family_id, child_name))
        return family_id, child_id

    @_synchronized
    def login(self, name: str, password: str, *, ttl: int = 86400) -> str | None:
        if len(password.encode()) > 1024:
            return None
        row = self.db.execute('SELECT id, salt, password_hash FROM families WHERE name=?', (name.strip(),)).fetchone()
        digest = self._password(password, row[1] if row else bytes(16))
        if not row or not hmac.compare_digest(digest, row[2]):
            return None
        token = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (self._token_hash(token), row[0], time.time() + ttl))
        return token

    @_synchronized
    def session_family(self, token: str) -> str | None:
        if not token or len(token) > 1024:
            return None
        row = self.db.execute('SELECT family_id FROM sessions WHERE token_hash=? AND expires>?', (self._token_hash(token), time.time())).fetchone()
        return row[0] if row else None

    @_synchronized
    def revoke_session(self, token: str) -> None:
        with self.db:
            self.db.execute('DELETE FROM sessions WHERE token_hash=?', (self._token_hash(token),))

    @_synchronized
    def children_for_family(self, family_id: str) -> list[str]:
        return [row[0] for row in self.db.execute('SELECT id FROM children WHERE family_id=? ORDER BY rowid', (family_id,))]

    @_synchronized
    def device_for_token(self, token: str) -> tuple[str, str, str] | None:
        if not token or len(token) > 1024:
            return None
        row = self.db.execute('SELECT id, family_id, child_id FROM devices WHERE token_hash=?', (self._token_hash(token),)).fetchone()
        return tuple(row) if row else None

    @_synchronized
    def owns_child(self, family_id: str, child_id: str) -> bool:
        return self.db.execute('SELECT 1 FROM children WHERE family_id=? AND id=?', (family_id, child_id)).fetchone() is not None

    @_synchronized
    def issue_device(self, family_id: str, child_id: str, device_id: str) -> str:
        if not self.owns_child(family_id, child_id) or not device_id.strip() or len(device_id) > 128:
            raise ValueError('孩子或设备无效')
        token = secrets.token_urlsafe(32)
        with self.db:
            result = self.db.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET child_id=excluded.child_id, token_hash=excluded.token_hash "
                "WHERE devices.family_id=excluded.family_id",
                (device_id, family_id, child_id, self._token_hash(token)))
            if result.rowcount != 1:
                raise PermissionError('设备已属于其他家庭')
        return token

    @_synchronized
    def authenticate_device(self, device_id: str, token: str) -> tuple[str, str] | None:
        if not token or len(token) > 1024:
            return None
        row = self.db.execute('SELECT family_id, child_id, token_hash FROM devices WHERE id=?', (device_id,)).fetchone()
        return (row[0], row[1]) if row and hmac.compare_digest(row[2], self._token_hash(token)) else None

    @_synchronized
    def revoke_device(self, family_id: str, device_id: str) -> bool:
        with self.db:
            result = self.db.execute('DELETE FROM devices WHERE family_id=? AND id=?', (family_id, device_id))
            return result.rowcount == 1
