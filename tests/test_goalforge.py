import tempfile
import unittest
from pathlib import Path
from taskpilot.goalforge import GoalForgeStore

class GoalForgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.app=GoalForgeStore(self.root/'db.sqlite3',self.root/'workspace')
    def tearDown(self): self.tmp.cleanup()
    def goal(self): return self.app.create_goal({'title':'Ship a test app','description':'Demo','target':'Working app','priority':'high'})
    def test_goal_crud_persists(self):
        g=self.goal(); self.assertEqual(self.app.update_goal(g['id'],{'title':'Ship it'})['title'],'Ship it')
        reopened=GoalForgeStore(self.root/'db.sqlite3',self.root/'workspace')
        self.assertEqual(reopened.goals()[0]['title'],'Ship it')
    def test_task_progress_and_dependency_guard(self):
        g=self.goal(); a=self.app.add_task(g['id'],{'title':'first','verification':'file exists'}); b=self.app.add_task(g['id'],{'title':'second','dependencies':[a['id']]})
        with self.assertRaises(ValueError): self.app.update_task(b['id'],{'status':'completed'})
        self.app.update_task(a['id'],{'status':'completed'});self.app.update_task(b['id'],{'status':'completed'})
        self.assertEqual(next(x for x in self.app.goals() if x['id']==g['id'])['progress'],100)
    def test_invalid_goal_and_dependency_rejected(self):
        with self.assertRaises(ValueError): self.app.create_goal({'title':' '})
        g=self.goal()
        with self.assertRaises(ValueError): self.app.add_task(g['id'],{'title':'bad','dependencies':['wrong']})
    def test_demo_executes_real_failure_recovery_and_persistent_lesson(self):
        r=self.app.demo();self.assertEqual(r['status'],'completed');self.assertTrue(r['verified']);self.assertIn('AssertionError',r['failure_output']);self.assertIn('PASS:',r['recovery_output'])
        reopened=GoalForgeStore(self.root/'db.sqlite3',self.root/'workspace')
        self.assertEqual(len(reopened.memory()['lessons']),1);self.assertEqual(len(reopened.memory()['attempts']),3)
        self.assertTrue(reopened.demo(reset=True)['status']=='reset')
    def test_lesson_is_unverified_by_default_and_deletable(self):
        lid=self.app.lesson('Remember this');self.assertFalse(self.app.memory()['lessons'][0]['verified']);self.assertTrue(self.app.delete_lesson(lid))
    def test_checkin_and_settings_are_persistent(self):
        self.app.settings({'daily_minutes':90,'working_hours':'10-18','checkin_frequency':'weekly'})
        self.assertEqual(self.app.settings()['daily_minutes'],90);self.assertIn('Manual',self.app.settings()['scheduler'])
        self.assertIn('active goal',self.app.checkin()['summary'])

if __name__=='__main__': unittest.main()
