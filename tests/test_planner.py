import unittest

from taskpilot.planner import plan_request


class PlannerTests(unittest.TestCase):
    def test_plans_file_creation_with_explicit_content(self):
        plan = plan_request("Create file notes/today.txt with content: Call Sam")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["actions"], [{"operation": "create_file", "path": "notes/today.txt", "content": "Call Sam"}])
        self.assertIn("Create", plan["summary"])

    def test_plans_read_without_mutation(self):
        plan = plan_request("Read file notes/today.txt")
        self.assertEqual(plan["actions"], [{"operation": "read_file", "path": "notes/today.txt"}])
        self.assertFalse(plan["requires_approval"])

    def test_marks_delete_as_approval_required(self):
        plan = plan_request("Delete file notes/old.txt")
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["requires_approval"])

    def test_rejects_ambiguous_or_unsupported_requests(self):
        plan = plan_request("Send an email to the team")
        self.assertEqual(plan["status"], "invalid")
        self.assertEqual(plan["actions"], [])

    def test_rejects_blank_request(self):
        self.assertEqual(plan_request("   ")["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
