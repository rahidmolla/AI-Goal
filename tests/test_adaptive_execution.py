import tempfile
import unittest
import threading
from pathlib import Path
from taskpilot.goalforge import GoalForgeStore
from taskpilot.orchestrator import Orchestrator


class AdaptiveExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'forge.sqlite3'
        self.workspace = self.root / 'workspace'
        self.store = GoalForgeStore(self.db, self.workspace)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_failure_trains_second_run_which_retrieves_and_applies_lesson(self):
        first = self.store.adaptive_demo()
        self.assertTrue(first['verified'])
        self.assertFalse(first['lesson_retrieved'])
        self.assertIn('AssertionError', first['failure_output'])
        self.assertIsNotNone(first['correction'])
        reopened = GoalForgeStore(self.db, self.workspace)
        second = reopened.adaptive_demo()
        self.assertTrue(second['lesson_retrieved'])
        self.assertTrue(second['verified'])
        self.assertIn('Applied verified lesson before execution', second['strategy'])
        self.assertIsNone(second['correction'])
        event = next(e for e in reopened.timeline() if e['kind'] == 'memory_retrieval' and e['details']['retrieved'])
        self.assertEqual(event['details']['decision'], second['strategy'])

    def test_adaptive_reset_removes_only_demo_data(self):
        other = self.store.create_goal({'title':'Keep this goal'})
        self.store.adaptive_demo()
        result = self.store.reset_adaptive_demo()
        self.assertEqual(result['status'], 'reset')
        self.assertIn(other['id'], [g['id'] for g in self.store.goals()])
        self.assertFalse(any(x['source'].startswith('adaptive-demo:') for x in self.store.memory()['lessons']))

    def test_demo_execution_respects_process_timeout_and_fixed_command(self):
        import inspect
        import taskpilot.goalforge as module
        self.assertIn('timeout=8', inspect.getsource(module.GoalForgeStore._adaptive_demo_claimed))
        self.assertIn('sys.executable', inspect.getsource(module.GoalForgeStore._adaptive_demo_claimed))
        self.assertIn("'test_demo.py'", inspect.getsource(module.GoalForgeStore._adaptive_demo_claimed))

    def test_independent_run_id_is_duplicate_protected(self):
        runner = Orchestrator(self.store)
        first = runner.run_adaptive_demo('once')
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(runner.run_adaptive_demo('once')['status'], 'duplicate')

    def test_cancel_before_execution_performs_no_tool_work(self):
        event = threading.Event()
        event.set()
        result = self.store.adaptive_demo(cancel_event=event)
        self.assertEqual(result['status'], 'cancelled')

    def test_concurrent_cross_instance_duplicate_is_rejected(self):
        # A persistent database lock also protects runs across separate store objects.
        second_store = GoalForgeStore(self.db, self.workspace)
        with self.store.db() as d:
            d.execute("INSERT INTO settings VALUES('adaptive_demo_lock','other-run')")
        result = second_store.adaptive_demo()
        self.assertEqual(result['status'], 'duplicate')
        self.assertEqual(len(second_store.goals()), 0)
