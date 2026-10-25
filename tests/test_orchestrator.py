import tempfile
import threading
import time
import unittest
from pathlib import Path
from taskpilot.goalforge import GoalForgeStore
from taskpilot.orchestrator import Orchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=GoalForgeStore(self.root/'f.sqlite3',self.root/'workspace')
        self.runner=Orchestrator(self.store,max_iterations=2)

    def tearDown(self): self.tmp.cleanup()

    def test_agent_loop_reuses_memory_and_records_decisions(self):
        first=self.runner.run_adaptive_demo()
        self.assertTrue(first['verified'])
        second=self.runner.run_adaptive_demo()
        self.assertTrue(second['lesson_retrieved'])
        self.assertIn('Applied verified lesson',second['strategy'])
        self.assertTrue(any(e['kind']=='memory_retrieval' for e in self.store.timeline()))

    def test_cancellation_before_execution_prevents_demo_actions(self):
        goal=self.store.create_goal({'title':'Blocked by dependency'})
        base=self.store.add_task(goal['id'],{'title':'first'})
        dependent=self.store.add_task(goal['id'],{'title':'wait','dependencies':[base['id']]})
        self.assertEqual(self.runner.execute_next(goal['id'])['status'],'awaiting_approval')
        self.assertEqual(self.store.adaptive_demo(threading.Event())['status'],'completed')

    def test_duplicate_action_is_rejected_while_run_active(self):
        entered=threading.Event();release=threading.Event();original=self.store.adaptive_demo
        def slow(cancel_event=None):
            entered.set();release.wait(2);return original(cancel_event)
        self.store.adaptive_demo=slow
        result=[]
        thread=threading.Thread(target=lambda:result.append(self.runner.run_adaptive_demo('same')))
        thread.start();self.assertTrue(entered.wait(1))
        self.assertEqual(self.runner.run_adaptive_demo('same')['status'],'duplicate')
        release.set();thread.join(3)
        self.assertEqual(result[0]['status'],'completed')

    def test_iteration_budget_is_bounded(self):
        self.assertEqual(Orchestrator(self.store,max_iterations=100).max_iterations,8)
        self.assertEqual(Orchestrator(self.store,max_iterations=0).run_adaptive_demo()['status'],'iteration_limit')

    def test_next_task_waits_for_dependencies(self):
        goal=self.store.create_goal({'title':'Dependency plan'})
        first=self.store.add_task(goal['id'],{'title':'first'})
        dependent=self.store.add_task(goal['id'],{'title':'dependent','dependencies':[first['id']]})
        self.assertEqual(self.runner.next_eligible_task(goal['id'])['id'],first['id'])
        self.store.update_task(first['id'],{'status':'completed'})
        self.assertEqual(self.runner.next_eligible_task(goal['id'])['id'],dependent['id'])

    def test_unknown_tools_and_approval_rejected(self):
        with self.assertRaises(ValueError): self.runner.register_tool('shell',lambda:None)
        with self.assertRaises(PermissionError): self.runner.register_tool('workspace.write_file',lambda:None,True)
