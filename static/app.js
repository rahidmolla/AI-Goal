const input = document.querySelector('#task-input');
const count = document.querySelector('#char-count');
const planButton = document.querySelector('#plan-button');
const message = document.querySelector('#message');
const resultSection = document.querySelector('#result');
const planCard = document.querySelector('#plan-card');
const report = document.querySelector('#report');
const badge = document.querySelector('#status-badge');
const historyList = document.querySelector('#history-list');
let currentTask = null;

const labels = {
  completed: 'Verified complete',
  failed: 'Failed',
  unverified: 'Unverified',
  approval_required: 'Needs approval',
  planned: 'Planned',
  ready: 'Ready to run',
  invalid: 'Can’t plan this yet'
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
  });
  let body;
  try { body = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}).`); }
  if (!response.ok && response.status !== 202 && response.status !== 422) {
    throw new Error(body.error || `Request failed (${response.status}).`);
  }
  return body;
}

function setMessage(text = '', isError = false) {
  message.textContent = text;
  message.classList.toggle('error', isError);
  message.hidden = !text;
}

function statusLabel(status) { return labels[status] || status.replaceAll('_', ' '); }
function make(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function renderPlan(task, pending = false) {
  currentTask = task;
  resultSection.hidden = false;
  planCard.replaceChildren();
  report.hidden = true;
  report.replaceChildren();
  const status = task.status || task.plan?.status || 'planned';
  badge.textContent = statusLabel(status);
  const title = make('strong', '', task.plan?.summary || task.summary || 'Task plan');
  planCard.append(title);
  const intro = make('p', '', task.plan?.status === 'invalid'
    ? 'No action was taken. Try one of the supported file task formats below.'
    : 'One small, explicit file action. Here’s the plan before I do it.');
  planCard.append(intro);
  const actions = task.plan?.actions || [];
  if (actions.length) {
    const list = make('ol');
    for (const action of actions) {
      let description = action.operation === 'create_file' ? `Create ${action.path}` : action.operation === 'read_file' ? `Read ${action.path}` : `Delete ${action.path}`;
      if (action.operation === 'create_file') description += ` (${new TextEncoder().encode(action.content).length} bytes of text)`;
      list.append(make('li', '', description));
    }
    planCard.append(list);
  }
  if (pending && task.id) {
    const approval = make('div', 'approval-box');
    const copy = make('span', 'approval-copy', 'This overwrites an existing file or removes one. Only continue if that’s what you want.');
    const button = make('button', 'primary-button approval-button', 'Approve & execute');
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = 'Working…';
      try {
        const updated = await api(`/api/tasks/${encodeURIComponent(task.id)}/approve`, { method: 'POST', body: JSON.stringify({ approve: true }) });
        renderPlan(updated);
        renderReport(updated);
        await loadHistory();
      } catch (error) {
        setMessage(error.message, true);
        button.disabled = false;
        button.textContent = 'Approve & execute';
      }
    });
    approval.append(copy, button);
    planCard.append(approval);
  }
  if (status === 'invalid') {
    planCard.append(make('p', '', 'Supported: “Create file PATH with content: TEXT”, “Read file PATH”, or “Delete file PATH”.'));
  }
  resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderReport(task) {
  if (!task || !task.result || ['planned', 'approval_required'].includes(task.status)) return;
  report.hidden = false;
  report.replaceChildren();
  report.append(make('strong', '', `Result · ${statusLabel(task.status)}`));
  report.append(make('p', '', task.result.summary || 'No completion summary was returned.'));
  for (const item of task.result.evidence || []) {
    const evidence = make('div', 'evidence-item');
    const line = make('span');
    const file = item.path ? `${item.path} · ` : '';
    line.append(make('strong', '', item.verified === false ? 'UNVERIFIED' : item.error ? 'FAILED' : item.check ? 'CHECKED' : 'EVIDENCE'));
    line.append(document.createTextNode(` ${file}${item.check || item.error || ''}`));
    evidence.append(line);
    if (item.content !== undefined) evidence.append(make('code', 'evidence-content', item.content));
    report.append(evidence);
  }
}

async function submitTask() {
  const request = input.value.trim();
  if (!request) { setMessage('Add a short description first.', true); input.focus(); return; }
  planButton.disabled = true;
  planButton.textContent = 'Planning…';
  setMessage();
  try {
    const plan = await api('/api/plan', { method: 'POST', body: JSON.stringify({ request }) });
    if (plan.status !== 'ready') {
      const rejected = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ request }) });
      renderPlan(rejected);
      renderReport(rejected);
      setMessage(plan.summary, true);
      await loadHistory();
      return;
    }
    renderPlan({ plan, status: 'ready' });
    const task = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ request }) });
    renderPlan(task, task.status === 'approval_required');
    renderReport(task);
    await loadHistory();
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    planButton.disabled = false;
    planButton.innerHTML = 'Make a plan <span aria-hidden="true">→</span>';
  }
}

function renderHistory(tasks) {
  historyList.replaceChildren();
  if (!tasks.length) {
    const empty = make('div', 'empty-state');
    empty.append(make('span', '', '✳'), make('p', '', 'No tasks yet. That’s a nice clean slate.'));
    historyList.append(empty);
    return;
  }
  for (const task of tasks) {
    const row = make('div', 'history-row');
    row.append(make('span', 'history-icon', task.status === 'completed' ? '✓' : task.status === 'failed' ? '!' : '↗'));
    const info = make('div');
    info.append(make('div', 'history-request', task.request));
    const date = new Date(task.updated_at);
    info.append(make('div', 'history-date', Number.isNaN(date.getTime()) ? task.updated_at : date.toLocaleString()));
    row.append(info, make('span', 'task-status', statusLabel(task.status)));
    row.addEventListener('click', () => { renderPlan(task, task.status === 'approval_required'); renderReport(task); });
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-label', `Open task: ${task.request}`);
    row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.click(); } });
    historyList.append(row);
  }
}

async function loadHistory() {
  try { const data = await api('/api/tasks'); renderHistory(data.tasks || []); }
  catch (error) {
    historyList.replaceChildren(make('div', 'empty-state', `History unavailable: ${error.message}`));
  }
}

input.addEventListener('input', () => { count.textContent = `${input.value.length.toLocaleString()} / 12,000`; });
input.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) submitTask(); });
planButton.addEventListener('click', submitTask);
document.querySelector('#refresh-button').addEventListener('click', loadHistory);
loadHistory();
