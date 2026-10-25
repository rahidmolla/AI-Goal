"""Bounded, auditable task orchestration for local GoalForge work."""
import threading


class Orchestrator:
    """Bounded runner for registered safe tools and the deterministic recovery demo."""
    MAX_ITERATIONS=8
    MAX_ATTEMPTS=2
    PROCESS_TIMEOUT_SECONDS=8
    # This orchestrator intentionally dispatches only the fixed recovery demo.
    # Other goal tasks are tracked, but remain human-driven rather than shell commands.
    ALLOWED_TOOLS={'allowlisted.python_test','workspace.write_file'}
    def __init__(self,store,max_iterations=MAX_ITERATIONS):
        self.store=store;self.max_iterations=max(0,min(int(max_iterations),self.MAX_ITERATIONS));self._lock=threading.Lock();self._runs={};self._completed_actions=set();self._results={}
    def cancel(self,run_id):
        with self._lock:
            event=self._runs.get(run_id)
            if not event:return False
            event.set();return True
    def register_tool(self,name,handler,requires_approval=False):
        if name not in self.ALLOWED_TOOLS: raise ValueError('Tool is not in the bounded registry.')
        if not callable(handler): raise ValueError('Tool handler must be callable.')
        if requires_approval: raise PermissionError('Sensitive tools require an explicit approval workflow.')
        return {'name':name,'registered':True,'requires_approval':False}
    def next_eligible_task(self,goal_id):
        goal=next((g for g in self.store.goals() if g['id']==goal_id),None)
        if not goal or goal['status']!='active':return None
        completed={t['id'] for t in goal['tasks'] if t['status']=='completed'}
        return next((t for t in goal['tasks'] if t['status'] in {'planned','in_progress'} and set(t['dependencies'])<=completed),None)
    def recover_demo(self):
        return self.run_adaptive_demo()
    def cancel_run(self,run_id):
        return self.cancel(run_id)

    def execute_next(self,goal_id,run_id=None):
        import uuid
        task=self.next_eligible_task(goal_id)
        if task is None:return {'status':'no_eligible_task','summary':'No dependency-ready task is eligible.'}
        if not any(w in task['title'].lower() for w in ('adaptive demo','recovery demo')):
            return {'status':'awaiting_approval','summary':'Arbitrary tasks cannot dispatch a tool without a registered plan and explicit approval.'}
        return self.run_adaptive_demo(run_id)
    def run_adaptive_demo(self,run_id=None):
        import uuid
        run_id=run_id or uuid.uuid4().hex
        with self._lock:
            if run_id in self._runs:return {'status':'duplicate','run_id':run_id,'summary':'An operation with this run ID is already in progress.'}
            if run_id in self._results:return {'status':'duplicate','run_id':run_id,'summary':'This run ID already completed; reuse is blocked to prevent duplicate actions.'}
            event=threading.Event();self._runs[run_id]=event
        try:
            if self.max_iterations<1:return {'status':'iteration_limit','run_id':run_id,'summary':'Iteration budget exhausted.'}
            if event.is_set():return {'status':'cancelled','run_id':run_id,'summary':'Cancelled before execution.'}
            result=self.store.adaptive_demo(cancel_event=event)
            result['run_id']=run_id;result['iteration_count']=min(1,self.max_iterations)
            with self._lock:self._results[run_id]=result
            return result
        finally:
            with self._lock:self._runs.pop(run_id,None)
