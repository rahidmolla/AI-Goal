import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class TaskStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, request TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def create(self, request, plan):
        import uuid
        task_id = uuid.uuid4().hex
        now = self._now()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)", (task_id, request, json.dumps(plan), "planned", "{}", now, now))
        return self.get(task_id)

    def get(self, task_id):
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._decode(row) if row else None

    def list(self, limit=50):
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ?", (max(1, min(int(limit), 100)),)).fetchall()
        return [self._decode(row) for row in rows]

    def update(self, task_id, status, result):
        now = self._now()
        with self._lock, self._connect() as db:
            cursor = db.execute("UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?", (status, json.dumps(result), now, task_id))
            if cursor.rowcount == 0:
                raise KeyError(task_id)
        return self.get(task_id)

    @staticmethod
    def _decode(row):
        return {"id": row["id"], "request": row["request"], "plan": json.loads(row["plan"]), "status": row["status"], "result": json.loads(row["result"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}
