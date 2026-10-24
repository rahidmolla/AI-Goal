import tempfile
import unittest
from pathlib import Path

from taskpilot.executor import execute_plan
from taskpilot.planner import plan_request


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_file_and_verifies_actual_content(self):
        result = execute_plan(self.root, plan_request("Create file notes/a.txt with content: hello"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual((self.root / "notes/a.txt").read_text(), "hello")
        self.assertTrue(result["evidence"])

    def test_overwrite_requires_approval_then_verifies(self):
        path = self.root / "a.txt"
        path.write_text("old")
        plan = plan_request("Create file a.txt with content: new")
        blocked = execute_plan(self.root, plan)
        self.assertEqual(blocked["status"], "approval_required")
        self.assertEqual(path.read_text(), "old")
        approved = execute_plan(self.root, plan, approved=True)
        self.assertEqual(approved["status"], "completed")
        self.assertEqual(path.read_text(), "new")

    def test_delete_requires_approval_and_verifies_removal(self):
        path = self.root / "a.txt"
        path.write_text("old")
        plan = plan_request("Delete file a.txt")
        blocked = execute_plan(self.root, plan)
        self.assertEqual(blocked["status"], "approval_required")
        self.assertTrue(path.exists())
        done = execute_plan(self.root, plan, approved=True)
        self.assertEqual(done["status"], "completed")
        self.assertFalse(path.exists())

    def test_read_reports_verified_content(self):
        path = self.root / "a.txt"
        path.write_text("hello")
        result = execute_plan(self.root, plan_request("Read file a.txt"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["evidence"][0]["content"], "hello")

    def test_missing_read_is_failed_not_completed(self):
        result = execute_plan(self.root, plan_request("Read file absent.txt"))
        self.assertEqual(result["status"], "failed")

    def test_bad_path_fails_safely(self):
        result = execute_plan(self.root, plan_request("Create file ../x with content: bad"))
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
