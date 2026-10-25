"""Single-owner cookie authentication for GoalForge.

Set GOALFORGE_OWNER_PASSWORD and GOALFORGE_SESSION_SECRET to enable workspace
access. Passwords are PBKDF2 hashed; session tokens are random, server-side,
and expire. Missing configuration fails closed.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time


class OwnerAuth:
    SESSION_SECONDS = 12 * 60 * 60

    def __init__(self, database_path):
        self.database_path = database_path
        self.password = os.environ.get("GOALFORGE_OWNER_PASSWORD", "")
        self.secret = os.environ.get("GOALFORGE_SESSION_SECRET", "")
        configured = bool(self.password and len(self.password) >= 12 and self.secret and len(self.secret) >= 32)
        self.enabled = configured
        self.password_hash = None
        if configured:
            self.password_hash = hashlib.pbkdf2_hmac("sha256", self.password.encode(), self._salt(), 310_000)
        else:
            # Even unconfigured deployments retain a recognizable auth gate and
            # no reusable default credentials; the workspace remains locked.
            self.secret = secrets.token_urlsafe(48)
            self.password = ""
        try:
            with self._db() as db:
                db.execute("CREATE TABLE IF NOT EXISTS owner_sessions (token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)")
        except sqlite3.Error:
            self.enabled = False
            self.password_hash = None
            self.password = ""
        return

    def _salt(self):
        return hmac.new(self.secret.encode(), b"goalforge-owner-password-v1", hashlib.sha256).digest()

    def _db(self):
        db = sqlite3.connect(self.database_path, timeout=8)
        db.execute("PRAGMA busy_timeout=8000")
        return db

    def verify_password(self, password):
        if not self.enabled or not isinstance(password, str):
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), self._salt(), 310_000)
        return hmac.compare_digest(candidate, self.password_hash)

    def create_session(self):
        if not self.enabled:
            raise RuntimeError("Owner authentication is not configured.")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = int(time.time())
        with self._db() as db:
            db.execute("DELETE FROM owner_sessions WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO owner_sessions VALUES(?,?,?)", (digest, now + self.SESSION_SECONDS, now))
        return token

    def authorized(self, token):
        if not self.enabled or not isinstance(token, str) or not token:
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            row = db.execute("SELECT expires_at FROM owner_sessions WHERE token_hash=?", (digest,)).fetchone()
        return bool(row and row[0] >= int(time.time()))

    def revoke(self, token):
        if not isinstance(token, str) or not token:
            return
        with self._db() as db:
            db.execute("DELETE FROM owner_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def clear_sessions(self):
        with self._db() as db:
            db.execute("DELETE FROM owner_sessions")
