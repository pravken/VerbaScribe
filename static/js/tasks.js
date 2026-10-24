// VerbaScribe page script: tasks from all recordings (package D, docs/redesign-plan.md). A first
// version: the open tasks as a list (the rows of the analysis, app.js commitmentRowHtml).
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

async function renderTasksPage() {
  const box = document.getElementById('tasks-list');
  const items = await fetch('/api/commitments').then(r => (r.ok ? r.json() : [])).catch(() => []);
  box.innerHTML = items.length ? items.map(c => commitmentRowHtml(c, true)).join('') + commitmentExportBar(null)
    : `<p class="py-10 text-center text-[12.5px] text-apple-tertiary">${escapeHtml(t('tasksEmpty'))}</p>`;
  badge('nav-tasks-n', items.length);
}

registerView('tasks', { show: renderTasksPage, refresh: renderTasksPage });
