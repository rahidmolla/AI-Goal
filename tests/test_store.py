import tempfile
import unittest
from pathlib import Path

from taskpilot.store import TaskStore


class StoreTests(unittest.TestCase):
    def test_history_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "tasks.sqlite3"
            store = TaskStore(db)
            record = store.create("Read file a.txt", {"status": "ready", "actions": []})
            store.update(record["id"], "completed", {"evidence": [{"path": "a.txt"}]})
            reopened = TaskStore(db)
            history = reopened.list()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["status"], "completed")
            self.assertEqual(history[0]["result"]["evidence"][0]["path"], "a.txt")

    def test_invalid_record_update_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp) / "tasks.sqlite3")
            with self.assertRaises(KeyError):
                store.update("missing", "failed", {})


if __name__ == "__main__":
    unittest.main()
