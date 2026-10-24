// VerbaScribe page script: the Tasks page (package D, docs/redesign-plan.md). The tasks of every
// recording (/api/tasks, app/commitments.py) as a list (by person, by due date, by recording) or a
// month calendar; who a task is on is a kind as well as a name (someone who speaks in the recording,
// someone only named, a department, another company, nobody). The Summary panel counts a period and
// asks the analysis model to tell it as text. «+ Задача» adds one by hand.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

const TK = {
  data: { tasks: [], people: [], meetings: {}, today: '' },
  loaded: false,
  view: 'list',            // list | cal
  by: 'person',            // person | due | record
  filter: 'open',          // open | mine | over | done | all
  people: [],              // casefolded names the list is limited to
  q: '',
  keep: new Set(),         // ticked here: they stay on screen until the filter or the view changes
  month: null,             // the first day of the month on the calendar (a Date)
  sel: null,               // the task whose card is open on the calendar
  day: null,               // the day chosen on the calendar (ISO)
  rep: true,               // the Summary panel is open
  repList: null,           // how it was in the list before the calendar folded it
  period: 'week',          // week | month | custom
  cFrom: '', cTo: '',
  incl: ['done', 'open', 'over'],
  fmt: 'short',            // short | people | mail
  report: null, reportBusy: false, sent: new Set(), copied: false,
  noteTimer: null,
};

const TK_KIND_ORDER = ['part', 'ext', 'team', 'org', 'none'];
const TK_COLORS = ['#64D2FF', '#FFB340', '#C9A2FF', '#7EE787', '#FF8A80', '#8CC4FF', '#FFD60A', '#5DE4C7'];
const TK_ME_KEY = 'vs-tasks-me';  // where «Это я» of the tasks was kept before the setting my_name

// Who the user is: the setting my_name (names separated by commas), shared with the home page and
// the people directory; tkMe() is the first of them, tkIsMe() checks any
function tkMeNames() {
  const v = String(((state.system && state.system.settings) || {}).my_name || '');
  return v.split(',').map(s => s.trim().toLocaleLowerCase()).filter(Boolean);
}
function tkMe() { return tkMeNames()[0] || ''; }
function tkIsMe(name) { return !!name && tkMeNames().includes(String(name).toLocaleLowerCase()); }

async function tkSaveMyName(value) {
  const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: { my_name: value } }) });
  const data = await readJson(res);
  if (!res.ok) { showToast(serverText(data.detail || 'Error')); return false; }
  if (state.system) state.system.settings = data.settings;
  return true;
}

// A choice made before the setting existed moves into it once
async function tkMigrateMe() {
  let old = '';
  try { old = localStorage.getItem(TK_ME_KEY) || ''; } catch { return; }
  if (!old) return;
  if (!tkMeNames().length && !await tkSaveMyName(old)) return;
  try { localStorage.removeItem(TK_ME_KEY); } catch { /* nothing to clean */ }
}

// --- Dates: the tasks speak in days from today ---
function tkToday() { return TK.data.today || tkIso(new Date()); }
function tkIso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function tkDate(iso) { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); }
function tkAddDays(iso, n) { const d = tkDate(iso); d.setDate(d.getDate() + n); return tkIso(d); }
function tkDays(iso) { return Math.round((tkDate(iso) - tkDate(tkToday())) / 86400000); }
function tkLocale() { return state.uiLang === 'en' ? 'en-GB' : 'ru-RU'; }
function tkShortDate(iso) {
  return tkDate(iso).toLocaleDateString(tkLocale(), { weekday: 'short', day: 'numeric', month: 'short' }).replace(/\.$/, '');
}
function tkMonday(iso) { const d = tkDate(iso); const wd = (d.getDay() + 6) % 7; d.setDate(d.getDate() - wd); return tkIso(d); }

function tkIsDone(task) { return task.status === 'done'; }
function tkIsOver(task) { return !tkIsDone(task) && task.due_date && task.due_date < tkToday(); }

function tkDueText(task) {
  if (!task.due_date) return t('tkNoDue');
  const n = tkDays(task.due_date);
  const label = n === 0 ? t('tkTodayLow') : n === 1 ? t('tkTomorrowLow') : tkShortDate(task.due_date);
  return tkIsOver(task) ? t('tkOverdueOn').replace('{date}', () => label) : label;
}

function tkDueTone(task) {
  if (tkIsDone(task) || !task.due_date) return '';
  const n = tkDays(task.due_date);
  return n < 0 ? 'tk-due-over' : n <= 1 ? 'tk-due-soon' : '';
}

function tkClock(sec) {
  if (sec == null) return '';
  const s = Math.floor(sec), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : String(m).padStart(2, '0')) + ':' + String(r).padStart(2, '0');
}

// --- Who: the look of an assignee by kind ---
function tkColorOf(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TK_COLORS[h % TK_COLORS.length];
}

function tkAvatar(name, kind, size = 22) {
  const k = name ? kind || 'ext' : 'none';
  const initial = name ? name.trim()[0].toLocaleUpperCase() : '?';
  const style = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.5)}px`;
  if (k === 'part') {
    const c = tkColorOf(name);
    return `<span class="tk-ava" style="${style};background:${c}2E;color:${c}">${escapeHtml(initial)}</span>`;
  }
  if (k === 'team') return `<span class="tk-ava tk-ava-team" style="${style}">▦</span>`;
  if (k === 'org') return `<span class="tk-ava tk-ava-org" style="${style}">◆</span>`;
  if (k === 'none') return `<span class="tk-ava tk-ava-none" style="${style}">?</span>`;
  return `<span class="tk-ava tk-ava-ext" style="${style}">${escapeHtml(initial)}</span>`;
}

function tkKindLabel(kind) {
  return { ext: t('tkKindExt'), team: t('tkKindTeam'), org: t('tkKindOrg') }[kind] || '';
}

function tkKindTitle(kind) {
  return { ext: t('tkKindExtTitle'), team: t('tkKindTeamTitle'), org: t('tkKindOrgTitle') }[kind] || '';
}

function tkWhoName(task) { return task.who || t('tkNobody'); }

function tkSourceText(task) {
  if (task.source === 'manual') return t('tkSrcManual');
  return t('tkSrcAi');
}

// --- Loading ---
async function loadTasks() {
  await tkMigrateMe();
  const res = await fetch('/api/tasks').catch(() => null);
  if (!res || !res.ok) return;
  TK.data = await res.json();
  TK.loaded = true;
  const open = TK.data.tasks.filter(x => !tkIsDone(x)).length;
  badge('nav-tasks-n', open);
}

async function renderTasksPage() {
  await loadTasks();
  renderTasks();
}

function renderTasks() {
  if (state.appView !== 'tasks') return;
  renderTasksHeader();
  renderTasksBar();
  if (TK.view === 'cal') renderTasksCalendar(); else renderTasksList();
  document.getElementById('tk-list').classList.toggle('hidden', TK.view === 'cal');
  document.getElementById('tk-cal').classList.toggle('hidden', TK.view !== 'cal');
  renderTasksReport();
}

// --- What is on screen: the filter, the people and the search (the counts of the filters use the same) ---
function tkMatchesPeopleAndSearch(task) {
  if (TK.people.length && !TK.people.includes((task.who || '').toLocaleLowerCase())) return false;
  const q = TK.q.trim().toLocaleLowerCase();
  if (q && !`${task.what} ${task.who} ${task.meeting_title} ${task.quote}`.toLocaleLowerCase().includes(q)) return false;
  return true;
}

function tkPassesFilter(task, filter) {
  switch (filter) {
    case 'open': return !tkIsDone(task);
    case 'mine': return !tkIsDone(task) && tkIsMe(task.who);
    case 'over': return tkIsOver(task);
    case 'done': return tkIsDone(task);
    default: return true;
  }
}

function tkShown() {
  return TK.data.tasks.filter(x => tkMatchesPeopleAndSearch(x) && (tkPassesFilter(x, TK.filter) || TK.keep.has(x.id)));
}

function renderTasksHeader() {
  const all = TK.data.tasks;
  const open = all.filter(x => !tkIsDone(x)).length, over = all.filter(tkIsOver).length;
  document.getElementById('tk-sub').innerHTML = escapeHtml(t('tkFromAll')) + ' · '
    + `<b>${escapeHtml(t('tkOpenN').replace('{n}', () => open))}</b> · `
    + `<span class="tk-red">${escapeHtml(t('tkOverN').replace('{n}', () => over))}</span> · `
    + escapeHtml(t('tkDoneN').replace('{n}', () => all.length - open));
}

function tkSeg(id, items, current, fn) {
  document.getElementById(id).innerHTML = items.map(([key, label, icon]) => {
    const on = key === current;
    return `<button role="radio" aria-checked="${on}" class="tk-seg-btn${on ? ' tk-on' : ''}" ${pageCall('click', fn, [key])}>${icon || ''}${escapeHtml(label)}</button>`;
  }).join('');
}

const TK_ICON_LIST = '<svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.5" cy="6.5" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="17.5" r="1"/></svg>';
const TK_ICON_CAL = '<svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';

function renderTasksBar() {
  tkSeg('tk-views', [['list', t('tkViewList'), TK_ICON_LIST], ['cal', t('tkViewCal'), TK_ICON_CAL]], TK.view, 'setTasksView');
  const groups = document.getElementById('tk-groups');
  groups.classList.toggle('hidden', TK.view === 'cal');
  tkSeg('tk-groups', [['person', t('tkByPerson')], ['due', t('tkByDue')], ['record', t('tkByRecord')]], TK.by, 'setTasksGroup');
  const base = TK.data.tasks.filter(tkMatchesPeopleAndSearch);
  const filters = [['open', t('tkFOpen')], ['mine', t('tkFMine')], ['over', t('tkFOver')], ['done', t('tkFDone')], ['all', t('tkFAll')]];
  document.getElementById('tk-filters').innerHTML = filters.map(([key, label]) => {
    const n = base.filter(x => tkPassesFilter(x, key)).length;
    const on = TK.filter === key;
    const title = key === 'mine' && !tkMe() ? ` title="${escapeHtml(t('tkMineHint'))}"` : '';
    return `<button class="tk-chip${on ? ' tk-on' : ''}" aria-pressed="${on}" ${pageCall('click', 'setTasksFilter', [key])}${title}>${escapeHtml(label)} <span class="tk-chip-n">${n}</span></button>`;
  }).join('');
  const btn = document.getElementById('tk-ppl-btn');
  const n = TK.people.length;
  const label = !n ? t('tkAllPeople') : n === 1 ? (tkPerson(TK.people[0])?.name || TK.people[0]) : t('tkNChosen').replace('{n}', () => n);
  btn.innerHTML = '<svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 19c0-3.3 2.7-5.6 6-5.6s6 2.3 6 5.6"/><path d="M16 4.6a3.2 3.2 0 0 1 0 6.2M18 13.6c1.9.8 3 2.6 3 5.4"/></svg>'
    + `<span>${escapeHtml(label)}</span><svg aria-hidden="true" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>`;
  btn.classList.toggle('tk-on', n > 0);
  document.getElementById('tk-ppl-clear').classList.toggle('hidden', !n);
}

function tkPerson(key) { return TK.data.people.find(p => p.name.toLocaleLowerCase() === key); }

function setTasksView(view) {
  if (view === TK.view) return;
  TK.keep.clear();
  if (view === 'cal') {  // the calendar needs the room: the Summary folds, and opens again in the list
    TK.repList = TK.rep;
    TK.rep = false;
    if (!TK.month) { const d = tkDate(tkToday()); TK.month = new Date(d.getFullYear(), d.getMonth(), 1); }
  } else if (TK.repList != null) {
    TK.rep = TK.repList;
    TK.repList = null;
  }
  TK.view = view;
  TK.sel = null;
  renderTasks();
}

function setTasksGroup(by) { TK.by = by; renderTasks(); }
function setTasksFilter(f) { TK.filter = f; TK.keep.clear(); renderTasks(); }
function onTasksSearch(value) { TK.q = value; TK.keep.clear(); renderTasks(); }
function clearTasksPeople() { TK.people = []; TK.report = null; renderTasks(); }

// --- The list ---
function tkGroups(items) {
  const groups = [];
  const add = (key, make) => {
    let g = groups.find(x => x.key === key);
    if (!g) { g = { key, items: [], ...make() }; groups.push(g); }
    return g;
  };
  if (TK.by === 'due') {
    const buckets = [
      ['over', t('tkBOver'), '#FF6961', x => tkIsOver(x)],
      ['soon', t('tkBSoon'), '#FFB340', x => x.due_date && !tkIsOver(x) && tkDays(x.due_date) <= 1],
      ['week', t('tkBWeek'), '#64B5FF', x => x.due_date && tkDays(x.due_date) >= 2 && x.due_date <= tkAddDays(tkMonday(tkToday()), 6)],
      ['later', t('tkBLater'), '#8E8E93', x => x.due_date && x.due_date > tkAddDays(tkMonday(tkToday()), 6)],
      ['nodue', t('tkBNoDue'), '#636366', () => true],
    ];
    for (const x of items) {
      const [key, title, color] = buckets.find(b => b[3](x));
      add(key, () => ({ title, color, order: buckets.findIndex(b => b[0] === key) })).items.push(x);
    }
    groups.sort((a, b) => a.order - b.order);
  } else if (TK.by === 'record') {
    for (const x of items) {
      add(String(x.meeting_id), () => ({ title: x.meeting_id ? x.meeting_title || t('tkUntitled') : t('tkNoRecord'),
        sub: x.meeting_date ? tkShortDate(x.meeting_date) : '', date: x.meeting_date || '' })).items.push(x);
    }
    groups.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  } else {
    for (const x of items) {
      const key = (x.who || '').toLocaleLowerCase();
      add(key, () => ({ person: true, name: x.who, kind: x.who ? x.who_kind : 'none', title: tkWhoName(x) })).items.push(x);
    }
    const mine = (g) => tkIsMe(g.key);
    groups.sort((a, b) => mine(b) - mine(a) || (!a.name) - (!b.name)
      || TK_KIND_ORDER.indexOf(a.kind) - TK_KIND_ORDER.indexOf(b.kind) || b.items.length - a.items.length);
  }
  for (const g of groups) g.items.sort((a, b) => tkIsDone(a) - tkIsDone(b) || (a.due_date || '9999').localeCompare(b.due_date || '9999'));
  return groups;
}

function renderTasksList() {
  const box = document.getElementById('tk-list');
  const items = tkShown();
  if (!TK.data.tasks.length) {
    box.innerHTML = `<div class="tk-empty"><b>${escapeHtml(t('tkEmptyTitle'))}</b><span>${escapeHtml(t('tkEmptyText'))}</span></div>`;
    return;
  }
  if (!items.length) {
    box.innerHTML = `<div class="tk-empty">${escapeHtml(t('tkNothing'))}</div>`;
    return;
  }
  box.innerHTML = tkGroups(items).map(g => {
    const all = TK.data.tasks.filter(x => TK.by === 'person' ? (x.who || '').toLocaleLowerCase() === g.key
      : TK.by === 'record' ? String(x.meeting_id) === g.key : g.items.includes(x));
    const done = all.filter(tkIsDone).length;
    const head = g.person ? tkAvatar(g.name, g.kind, 24) : TK.by === 'due' ? `<span class="tk-dot" style="background:${g.color}"></span>` : '';
    const kind = g.person && tkKindLabel(g.kind) ? `<span class="tk-kind" title="${escapeHtml(tkKindTitle(g.kind))}">${escapeHtml(tkKindLabel(g.kind))}</span>` : '';
    const me = g.person && g.key && tkIsMe(g.key) ? `<span class="tk-kind tk-kind-me">${escapeHtml(t('tkItsMe'))}</span>` : '';
    const sub = g.sub || t('tkGroupSub').replace('{open}', () => all.length - done).replace('{done}', () => done);
    const pct = all.length ? Math.round((done / all.length) * 100) : 0;
    return `<section class="tk-group">
      <div class="tk-group-head">${head}<span class="tk-group-title">${escapeHtml(g.title)}</span>${kind}${me}
        <span class="tk-group-sub">${escapeHtml(sub)}</span><span class="tk-grow"></span>
        <span class="tk-progress" title="${escapeHtml(t('tkDonePct').replace('{n}', () => pct))}"><span style="width:${pct}%"></span></span></div>
      ${g.items.map(tkRowHtml).join('')}
    </section>`;
  }).join('');
}

function tkRowHtml(x) {
  const done = tkIsDone(x);
  const rec = x.meeting_id
    ? `<button class="tk-src" ${pageCall('click', 'openTaskSource', [x.id])} title="${escapeHtml(t('tkOpenAt'))}">${escapeHtml(x.meeting_title || t('tkUntitled'))}${x.at != null ? ` · <span class="tk-mono">${tkClock(x.at)}</span>` : ''}</button>`
    : `<span class="tk-src tk-src-none">${escapeHtml(t('tkNoRecord'))}</span>`;
  const kind = x.who && tkKindLabel(x.who_kind) ? `<span class="tk-kind">${escapeHtml(tkKindLabel(x.who_kind))}</span>` : '';
  return `<div class="tk-row${done ? ' tk-done' : ''}" data-tid="${x.id}">
    <input type="checkbox" ${done ? 'checked' : ''} class="tk-check" ${pageCall('change', 'toggleTaskDone', [x.id, { $: 'checked' }])} aria-label="${escapeHtml(t('tkDone'))}">
    <span class="tk-row-main"><span class="tk-row-text" title="${escapeHtml(x.what)}">${escapeHtml(x.what)}</span>${rec}</span>
    <button class="tk-who" ${pageCall('click', 'openTaskAssign', [x.id, { $: 'event' }])} title="${escapeHtml(t('tkAssignTitle'))}" aria-haspopup="true">
      ${tkAvatar(x.who, x.who_kind, 20)}<span class="tk-who-name${x.who ? '' : ' tk-dim'}">${escapeHtml(tkWhoName(x))}</span>${kind}</button>
    <button class="tk-due ${tkDueTone(x)}" ${pageCall('click', 'openTaskDue', [x.id, { $: 'event' }])} title="${escapeHtml(t('tkDueTitle'))}">${escapeHtml(tkDueText(x))}</button>
    <span class="tk-from" title="${escapeHtml(t('tkFromTitle'))}">${escapeHtml(tkSourceText(x))}</span>
    <button class="tk-more" ${pageCall('click', 'openTaskMenu', [x.id, { $: 'event' }])} aria-label="${escapeHtml(t('tkMore'))}" title="${escapeHtml(t('tkMore'))}">⋯</button>
  </div>`;
}

function tkTask(id) { return TK.data.tasks.find(x => x.id === id); }

async function tkPatch(id, changes) {
  const res = await fetch(`/api/tasks/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    showToast(serverText(err.detail) || t('tkSaveFailed'));
    return null;
  }
  const task = await res.json();
  const i = TK.data.tasks.findIndex(x => x.id === id);
  if (i >= 0) TK.data.tasks[i] = task;
  return task;
}

async function tkRefreshPeople() {
  await loadTasks();
  renderTasks();
}

async function toggleTaskDone(id, checked) {
  TK.keep.add(id);
  const task = await tkPatch(id, { status: checked ? 'done' : 'open' });
  if (!task) { renderTasks(); return; }
  TK.report = null;
  badge('nav-tasks-n', TK.data.tasks.filter(x => !tkIsDone(x)).length);
  renderTasks();
}

// The moment of the recording the task came from: the recording opens there
function openTaskSource(id) {
  const x = tkTask(id);
  if (!x || !x.meeting_id) return;
  switchAppView('library');
  selectMeetingAndSeek(x.meeting_id, x.at || 0);
}

// --- Popovers: one at a time, kept inside the window ---
function tkClosePop() { document.getElementById('tk-pop')?.remove(); }

function tkOpenPop(html, anchor, width = 320) {
  tkClosePop();
  const pop = document.createElement('div');
  pop.id = 'tk-pop';
  pop.className = 'tk-pop';
  pop.style.width = `${width}px`;
  pop.innerHTML = html;
  document.body.appendChild(pop);
  // the anchor is a point of the window; the popover opens under it, or above it when there is no room
  const r = anchor.getBoundingClientRect();
  const h = pop.getBoundingClientRect().height / uiZoom();
  const below = zpx(r.bottom) + 6, above = zpx(r.top) - 6 - h;
  const top = below + h <= viewH() - 8 || above < 8 ? Math.min(below, viewH() - h - 8) : above;
  pop.style.top = `${Math.max(8, top)}px`;
  pop.style.left = `${Math.max(8, Math.min(zpx(r.right) - width, viewW() - width - 8))}px`;
  return pop;
}

// A click outside closes it (its own buttons go through the page's handlers, which run first)
document.addEventListener('click', ev => { if (!ev.target.closest?.('#tk-pop')) tkClosePop(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') tkClosePop(); });
window.addEventListener('resize', () => tkClosePop());

// --- Who a task is on: the people of its recording first, then everyone else, or a new name ---
function tkAssignOptions(x) {
  const parts = (TK.data.meetings[String(x.meeting_id)]?.participants || []);
  const seen = new Set(parts.map(n => n.toLocaleLowerCase()));
  const groups = [[t('tkGParts'), parts.map(name => ({ name, kind: 'part' }))]];
  const others = TK.data.people.filter(p => !seen.has(p.name.toLocaleLowerCase()));
  groups.push([t('tkGPeople'), others.filter(p => p.kind === 'part' || p.kind === 'ext')]);
  groups.push([t('tkGTeams'), others.filter(p => p.kind === 'team')]);
  groups.push([t('tkGOrgs'), others.filter(p => p.kind === 'org')]);
  return groups.filter(([, list]) => list.length);
}

function openTaskAssign(id, e) {
  e.stopPropagation();
  const x = tkTask(id);
  if (!x) return;
  const quote = x.quote ? `<div class="tk-pop-quote">${escapeHtml(t('tkInRecord'))} «${escapeHtml(x.quote)}»${x.at != null ? ` <span class="tk-mono">${tkClock(x.at)}</span>` : ''}</div>` : '';
  const opt = (name, kind) => {
    const on = (x.who || '').toLocaleLowerCase() === (name || '').toLocaleLowerCase();
    return `<button class="tk-pop-item" role="menuitemradio" aria-checked="${on}" ${pageCall('click', 'assignTask', [id, name, kind])}>
      ${tkAvatar(name, kind, 20)}<span class="tk-grow">${escapeHtml(name || t('tkNobody'))}</span>${on ? '<span class="tk-tick">✓</span>' : ''}</button>`;
  };
  const html = quote + tkAssignOptions(x).map(([title, list]) =>
    `<div class="tk-pop-cap">${escapeHtml(title)}</div>${list.map(p => opt(p.name, p.kind)).join('')}`).join('')
    + `<div class="tk-pop-cap"></div>${opt('', 'none')}`
    + `<div class="tk-pop-add">
        <div class="tk-row2"><input id="tk-pop-name" class="tk-input" type="text" data-i18n-placeholder="tkTypeWho" placeholder="${escapeHtml(t('tkTypeWho'))}" title="${escapeHtml(t('tkAssignTitle'))}"
          ${pageCall('keydown', 'onAssignKey', [id, { $: 'event' }])}>
        <button class="tk-btn tk-btn-primary tk-btn-sm" ${pageCall('click', 'assignTyped', [id])}>${escapeHtml(t('tkAssignBtn'))}</button></div>
        <div class="tk-row2" role="radiogroup">${tkKindChips('tk-pop-kind', 'ext')}<span class="tk-hint">${escapeHtml(t('tkRemembered'))}</span></div>
      </div>`;
  tkOpenPop(html, e.currentTarget, 320);
  setTimeout(() => document.getElementById('tk-pop-name')?.focus(), 20);
}

function tkKindChips(group, current) {
  return [['ext', t('tkKPerson')], ['team', t('tkKTeam')], ['org', t('tkKOrg')]].map(([k, label]) =>
    `<button type="button" role="radio" aria-checked="${k === current}" data-kind="${k}" class="tk-mini${k === current ? ' tk-on' : ''}"
      ${pageCall('click', 'pickKindChip', [group, k])}>${escapeHtml(label)}</button>`).join('');
}

function pickKindChip(group, kind) {
  const box = document.getElementById(group) || document.querySelector(`[data-group="${group}"]`);
  const holder = box || document.getElementById('tk-pop');
  holder?.querySelectorAll('.tk-mini[data-kind]').forEach(b => {
    const on = b.dataset.kind === kind;
    b.classList.toggle('tk-on', on);
    b.setAttribute('aria-checked', String(on));
  });
  if (group === 'tk-n-kinds') { TKN.kind = kind; renderNewTaskWho(); }
}

function onAssignKey(id, e) {
  if (e.key === 'Enter') { e.preventDefault(); assignTyped(id); }
}

function assignTyped(id) {
  const name = (document.getElementById('tk-pop-name')?.value || '').trim();
  if (!name) return;
  const kind = document.querySelector('#tk-pop .tk-mini.tk-on')?.dataset.kind || 'ext';
  const known = TK.data.people.find(p => p.name.toLocaleLowerCase() === name.toLocaleLowerCase());
  assignTask(id, known ? known.name : name, known ? known.kind : kind);
}

async function assignTask(id, name, kind) {
  tkClosePop();
  const task = await tkPatch(id, { who: name, who_kind: name ? kind : 'none' });
  if (task) { TK.report = null; await tkRefreshPeople(); }
}

// --- The due date: the near days, a date, or none ---
function tkDueChoices() {
  const today = tkToday();
  const monday = tkAddDays(tkMonday(today), 7);
  return [[t('tkToday'), today], [t('tkTomorrow'), tkAddDays(today, 1)], [t('tkMonday'), monday],
    [t('tkInWeek'), tkAddDays(today, 7)], [t('tkNoDue'), '']];
}

function openTaskDue(id, e) {
  e.stopPropagation();
  const x = tkTask(id);
  if (!x) return;
  const html = tkDueChoices().map(([label, iso]) => {
    const on = (x.due_date || '') === iso;
    return `<button class="tk-pop-item" role="menuitemradio" aria-checked="${on}" ${pageCall('click', 'setTaskDue', [id, iso])}>
      <span class="tk-grow">${escapeHtml(label)}</span><span class="tk-dim">${iso ? escapeHtml(tkShortDate(iso)) : ''}</span>${on ? '<span class="tk-tick">✓</span>' : ''}</button>`;
  }).join('') + `<div class="tk-pop-add"><input type="date" class="tk-input" value="${escapeHtml(x.due_date || '')}" title="${escapeHtml(t('tkOtherDate'))}"
      ${pageCall('change', 'setTaskDue', [id, { $: 'value' }])}></div>`;
  tkOpenPop(html, e.currentTarget, 240);
}

async function setTaskDue(id, iso) {
  tkClosePop();
  const task = await tkPatch(id, { due_date: iso || '' });
  if (task) { TK.report = null; renderTasks(); }
}

// --- A task's own menu: change its words, delete it ---
function openTaskMenu(id, e) {
  e.stopPropagation();
  const r = e.currentTarget.getBoundingClientRect();
  openTextMenuAt([
    { label: t('tkEditText'), run: () => editTaskText(id) },
    { label: t('tkOpenRecord'), run: () => openTaskSource(id), disabled: !tkTask(id)?.meeting_id },
    { sep: true },
    { label: t('tkDelete'), run: () => deleteTask(id) },
  ], r.right - 220, r.bottom + 4);
}

function editTaskText(id) {
  const row = document.querySelector(`.tk-row[data-tid="${id}"] .tk-row-text`);
  const x = tkTask(id);
  if (!row || !x) return;
  const input = document.createElement('input');
  input.className = 'tk-input tk-row-edit';
  input.value = x.what;
  input.title = t('tkEditText');
  row.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (save && v && v !== x.what) await tkPatch(id, { what: v });
    renderTasks();
  };
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

async function deleteTask(id) {
  const x = tkTask(id);
  if (!x) return;
  const ok = await confirmDialog(t('tkDeleteTitle'), t('tkDeleteText').replace('{task}', () => x.what), t('tkDelete'));
  if (!ok) return;
  const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
  if (!res.ok) { showToast(t('tkSaveFailed')); return; }
  TK.data.tasks = TK.data.tasks.filter(y => y.id !== id);
  if (TK.sel === id) TK.sel = null;
  TK.report = null;
  await tkRefreshPeople();
}

// --- The people filter: several people or departments at once; and who "me" is ---
function openTasksPeopleMenu(e) {
  e.stopPropagation();
  tkRenderPeopleMenu(e.currentTarget);
}

function tkRenderPeopleMenu(anchor) {
  const groups = [[t('tkGParts'), 'part'], [t('tkGMentioned'), 'ext'], [t('tkGTeams'), 'team'], [t('tkGOrgs'), 'org']];
  const rows = groups.map(([title, kind]) => {
    const list = TK.data.people.filter(p => p.kind === kind);
    if (!list.length) return '';
    return `<div class="tk-pop-cap">${escapeHtml(title)}</div>` + list.map(p => {
      const key = p.name.toLocaleLowerCase(), on = TK.people.includes(key);
      const isMe = tkIsMe(key);
      const meBtn = kind === 'part' || kind === 'ext'
        ? `<button class="tk-mini tk-me-btn${isMe ? ' tk-on' : ''}" ${pageCall('click', 'setTasksMe', [p.name])} title="${escapeHtml(t('tkItsMeTitle'))}">${escapeHtml(t('tkItsMe'))}</button>` : '';
      return `<label class="tk-pop-item"><input type="checkbox" ${on ? 'checked' : ''} ${pageCall('change', 'toggleTasksPerson', [key])}>
        ${tkAvatar(p.name, p.kind, 20)}<span class="tk-grow">${escapeHtml(p.name)}</span>${meBtn}<span class="tk-dim">${p.open || ''}</span></label>`;
    }).join('');
  }).join('');
  const nobody = TK.data.tasks.filter(x => !x.who && !tkIsDone(x)).length;
  const html = (rows || `<div class="tk-pop-quote">${escapeHtml(t('tkNoPeople'))}</div>`)
    + (nobody ? `<div class="tk-pop-cap"></div><label class="tk-pop-item"><input type="checkbox" ${TK.people.includes('') ? 'checked' : ''} ${pageCall('change', 'toggleTasksPerson', [''])}>
        ${tkAvatar('', 'none', 20)}<span class="tk-grow">${escapeHtml(t('tkNobody'))}</span><span class="tk-dim">${nobody}</span></label>` : '');
  const pop = tkOpenPop(html, anchor, 300);
  pop.classList.add('tk-pop-scroll');
  const r = anchor.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(zpx(r.left), viewW() - 308))}px`;
}

function toggleTasksPerson(key) {
  TK.people = TK.people.includes(key) ? TK.people.filter(k => k !== key) : TK.people.concat(key);
  TK.keep.clear();
  TK.report = null;
  renderTasks();
  tkRenderPeopleMenu(document.getElementById('tk-ppl-btn'));
}

async function setTasksMe(name) {
  const rest = tkMeNames().filter(n => n !== name.toLocaleLowerCase());
  const off = tkIsMe(name);
  const names = String(((state.system && state.system.settings) || {}).my_name || '').split(',').map(s => s.trim()).filter(Boolean);
  if (!await tkSaveMyName(off ? names.filter(n => rest.includes(n.toLocaleLowerCase())).join(', ') : name)) return;
  renderTasks();
  tkRenderPeopleMenu(document.getElementById('tk-ppl-btn'));
}

// --- Export: what is on screen, for another app ---
function openTasksExportMenu(e) {
  e.stopPropagation();
  const r = e.currentTarget.getBoundingClientRect();
  const n = tkShown().length;
  openTextMenuAt([
    { label: t('tkExpTodoist'), key: 'CSV', run: () => exportTasks('todoist'), disabled: !n },
    { label: t('tkExpNotion'), key: 'Markdown', run: () => exportTasks('md'), disabled: !n },
    { label: t('tkExpObsidian'), run: () => exportTasks('obsidian'), disabled: !n },
    { label: t('tkExpTable'), key: 'CSV', run: () => exportTasks('csv'), disabled: !n },
  ], r.right - 240, r.bottom + 4);
}

async function exportTasks(fmt) {
  const ids = tkShown().map(x => x.id);
  const res = await fetch('/api/tasks/export', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fmt, ids, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(serverText(out.detail) || t('tkSaveFailed')); return; }
  if (out.cancelled) return;
  tkNote(t('tkExported').replace('{n}', () => ids.length));
}

function tkNote(text) {
  const el = document.getElementById('tk-note');
  el.textContent = '✓ ' + text;
  el.classList.remove('hidden');
  clearTimeout(TK.noteTimer);
  TK.noteTimer = setTimeout(() => el.classList.add('hidden'), 4000);
}

// --- The month calendar: drag a task to another day, or to "no due date" ---
function renderTasksCalendar() {
  const box = document.getElementById('tk-cal');
  const items = tkShown();
  const first = TK.month;
  const start = tkMonday(tkIso(first));
  const lastDay = new Date(first.getFullYear(), first.getMonth() + 1, 0);
  const weeks = Math.ceil((tkDays(tkIso(lastDay)) - tkDays(start) + 1) / 7);
  const today = tkToday();
  const monthName = first.toLocaleDateString(tkLocale(), { month: 'long', year: 'numeric' });
  const days = [];
  for (let i = 0; i < weeks * 7; i++) days.push(tkAddDays(start, i));
  const dayNames = [...Array(7)].map((_, i) => tkDate(tkAddDays(start, i)).toLocaleDateString(tkLocale(), { weekday: 'short' }).replace(/\.$/, ''));
  const chip = (x) => `<div class="tk-cchip${tkIsDone(x) ? ' tk-done' : ''}${TK.sel === x.id ? ' tk-sel' : ''}" draggable="true"
      ${pageCall('dragstart', 'onTaskDragStart', [x.id, { $: 'event' }])} ${pageCall('click', 'selectCalTask', [x.id, { $: 'event' }])} title="${escapeHtml(x.what + ' — ' + tkWhoName(x))}"
      style="border-left-color:${x.who && x.who_kind === 'part' ? tkColorOf(x.who) : 'rgba(255,255,255,0.25)'}">
      <button class="tk-cbox${tkIsDone(x) ? ' tk-on' : ''}" ${pageCall('click', 'toggleCalTask', [x.id, { $: 'event' }])} aria-label="${escapeHtml(t('tkDone'))}">${tkIsDone(x) ? '✓' : ''}</button>
      ${tkAvatar(x.who, x.who_kind, 15)}<span class="tk-ctext">${escapeHtml(x.what)}</span></div>`;
  const cells = days.map(iso => {
    const list = items.filter(x => x.due_date === iso);
    const d = tkDate(iso);
    const out = d.getMonth() !== first.getMonth();
    const cls = ['tk-day', out ? 'tk-out' : '', iso === today ? 'tk-today' : '', iso === TK.day ? 'tk-chosen' : '', [0, 6].includes(d.getDay()) ? 'tk-weekend' : ''].filter(Boolean).join(' ');
    const more = list.length > 3 ? `<button class="tk-more-n" ${pageCall('click', 'openCalDay', [iso])}>+${list.length - 3}</button>` : '';
    return `<div class="${cls}" data-day="${iso}" ${pageCall('click', 'openCalDay', [iso])} ${pageCall('dragover', 'onCalDragOver', [{ $: 'event' }])}
        ${pageCall('dragleave', 'onCalDragLeave', [{ $: 'event' }])} ${pageCall('drop', 'onCalDrop', [iso, { $: 'event' }])} title="${escapeHtml(t('tkDayTitle'))}">
      <div class="tk-day-head"><span class="tk-day-n">${d.getDate()}</span>${more}</div>
      ${list.slice(0, 3).map(chip).join('')}</div>`;
  }).join('');
  box.innerHTML = `<div class="tk-cal-main">
      <div class="tk-cal-head">
        <button class="tk-x" ${pageCall('click', 'shiftTasksMonth', [-1])} title="${escapeHtml(t('tkPrevMonth'))}" aria-label="${escapeHtml(t('tkPrevMonth'))}">‹</button>
        <button class="tk-x" ${pageCall('click', 'shiftTasksMonth', [1])} title="${escapeHtml(t('tkNextMonth'))}" aria-label="${escapeHtml(t('tkNextMonth'))}">›</button>
        <h2 class="tk-month">${escapeHtml(monthName[0].toLocaleUpperCase() + monthName.slice(1))}</h2>
        <button class="tk-btn tk-btn-sm" data-click="tasksToToday">${escapeHtml(t('tkTodayBtn'))}</button>
        <span class="tk-grow"></span><span class="tk-hint">${escapeHtml(t('tkCalHint'))}</span>
      </div>
      <div class="tk-week">${dayNames.map((n, i) => `<span class="${i > 4 ? 'tk-dim2' : ''}">${escapeHtml(n.toLocaleUpperCase())}</span>`).join('')}</div>
      <div class="tk-grid" style="grid-template-rows:repeat(${weeks}, minmax(0, 1fr))">${cells}</div>
    </div>
    <aside class="tk-cal-side" ${pageCall('dragover', 'onCalDragOver', [{ $: 'event' }])} ${pageCall('dragleave', 'onCalDragLeave', [{ $: 'event' }])} ${pageCall('drop', 'onCalDrop', ['', { $: 'event' }])}>
      ${tkSelCard()}${tkDayList(items)}${tkNoDueList(items)}
    </aside>`;
}

function tkSelCard() {
  const x = TK.sel != null ? tkTask(TK.sel) : null;
  if (!x) return '';
  const kind = x.who && tkKindLabel(x.who_kind) ? `<span class="tk-kind">${escapeHtml(tkKindLabel(x.who_kind))}</span>` : '';
  return `<section class="tk-card" aria-label="${escapeHtml(t('tkTask'))}">
    <div class="tk-card-head"><input type="checkbox" class="tk-check" ${tkIsDone(x) ? 'checked' : ''} ${pageCall('change', 'toggleTaskDone', [x.id, { $: 'checked' }])} aria-label="${escapeHtml(t('tkDone'))}">
      <span class="tk-card-text${tkIsDone(x) ? ' tk-strike' : ''}">${escapeHtml(x.what)}</span>
      <button class="tk-x" data-click="closeCalTask" title="${escapeHtml(t('tkClose'))}">✕</button></div>
    <dl class="tk-dl">
      <dt>${escapeHtml(t('tkWho'))}</dt><dd><button class="tk-who" ${pageCall('click', 'openTaskAssign', [x.id, { $: 'event' }])}>${tkAvatar(x.who, x.who_kind, 18)}<span>${escapeHtml(tkWhoName(x))}</span>${kind}</button></dd>
      <dt>${escapeHtml(t('tkDue'))}</dt><dd><button class="tk-due ${tkDueTone(x)}" ${pageCall('click', 'openTaskDue', [x.id, { $: 'event' }])}>${escapeHtml(tkDueText(x))}</button></dd>
      <dt>${escapeHtml(t('tkRecord'))}</dt><dd class="tk-ell" title="${escapeHtml(x.meeting_title || '')}">${escapeHtml(x.meeting_id ? x.meeting_title || t('tkUntitled') : t('tkNoRecord'))}</dd>
      <dt>${escapeHtml(t('tkFrom'))}</dt><dd class="tk-dim">${escapeHtml(x.source === 'manual' ? t('tkFromManual') : t('tkFromAi'))}</dd>
    </dl>
    ${x.quote ? `<blockquote class="tk-quote">«${escapeHtml(x.quote)}»${x.at != null ? ` <span class="tk-mono">${tkClock(x.at)}</span>` : ''}</blockquote>` : ''}
    <div class="tk-row2">
      ${x.meeting_id ? `<button class="tk-btn tk-btn-light tk-grow" ${pageCall('click', 'openTaskSource', [x.id])}>▶ ${escapeHtml(t('tkOpenAtShort'))}</button>` : ''}
      <button class="tk-btn" ${pageCall('click', 'showTaskInList', [x.id])} title="${escapeHtml(t('tkToListTitle'))}">${escapeHtml(t('tkToList'))}</button>
    </div>
  </section>`;
}

function tkDayList(items) {
  if (!TK.day) return '';
  const list = items.filter(x => x.due_date === TK.day);
  const title = tkDate(TK.day).toLocaleDateString(tkLocale(), { weekday: 'long', day: 'numeric', month: 'long' });
  const open = list.filter(x => !tkIsDone(x)).length;
  return `<section class="tk-dayl" aria-label="${escapeHtml(title)}">
    <div class="tk-dayl-head"><b>${escapeHtml(title[0].toLocaleUpperCase() + title.slice(1))}</b><span class="tk-dim">${escapeHtml(t('tkDaySub').replace('{n}', () => list.length).replace('{open}', () => open))}</span>
      <span class="tk-grow"></span><button class="tk-x" data-click="closeCalDay" title="${escapeHtml(t('tkClose'))}">✕</button></div>
    ${list.map(x => `<div class="tk-dchip${tkIsDone(x) ? ' tk-done' : ''}" ${pageCall('click', 'selectCalTask', [x.id, { $: 'event' }])}>
        <button class="tk-cbox${tkIsDone(x) ? ' tk-on' : ''}" ${pageCall('click', 'toggleCalTask', [x.id, { $: 'event' }])} aria-label="${escapeHtml(t('tkDone'))}">${tkIsDone(x) ? '✓' : ''}</button>
        <span class="tk-grow"><span class="tk-dtext">${escapeHtml(x.what)}</span><span class="tk-dim tk-small">${escapeHtml(tkWhoName(x))}</span></span></div>`).join('')}
    ${list.length ? '' : `<span class="tk-hint">${escapeHtml(t('tkDayNone'))}</span>`}
  </section>`;
}

function tkNoDueList(items) {
  const list = items.filter(x => !x.due_date);
  return `<div class="tk-nodue-head"><span class="tk-cap">${escapeHtml(t('tkNoDueCap'))}</span><span class="tk-dim">${list.length}</span></div>
    ${list.map(x => `<div class="tk-dchip${tkIsDone(x) ? ' tk-done' : ''}" draggable="true" ${pageCall('dragstart', 'onTaskDragStart', [x.id, { $: 'event' }])}
        ${pageCall('click', 'selectCalTask', [x.id, { $: 'event' }])}>
        <span class="tk-grow"><span class="tk-dtext">${escapeHtml(x.what)}</span><span class="tk-dim tk-small">${tkAvatar(x.who, x.who_kind, 14)} ${escapeHtml(tkWhoName(x))}</span></span></div>`).join('')}
    <span class="tk-hint">${escapeHtml(t('tkNoDueHint'))}</span>`;
}

function shiftTasksMonth(n) { TK.month = new Date(TK.month.getFullYear(), TK.month.getMonth() + n, 1); renderTasks(); }
function tasksToToday() { const d = tkDate(tkToday()); TK.month = new Date(d.getFullYear(), d.getMonth(), 1); TK.day = tkToday(); renderTasks(); }

// A click on a day: its tasks on the right, the day marked on the calendar
function openCalDay(iso) { TK.day = iso; TK.sel = null; renderTasks(); }
function closeCalDay() { TK.day = null; renderTasks(); }

function selectCalTask(id, e) {
  e.stopPropagation();
  TK.sel = id;
  const x = tkTask(id);
  if (x?.due_date) TK.day = x.due_date;
  renderTasks();
}

function closeCalTask() { TK.sel = null; renderTasks(); }

function toggleCalTask(id, e) {
  e.stopPropagation();
  const x = tkTask(id);
  if (x) toggleTaskDone(id, !tkIsDone(x));
}

function showTaskInList(id) {
  const x = tkTask(id);
  setTasksView('list');
  TK.filter = 'all';
  TK.people = [];
  TK.q = x ? x.what : '';
  document.getElementById('tk-q').value = TK.q;
  renderTasks();
}

function onTaskDragStart(id, e) {
  e.dataTransfer.setData('text/x-vs-task', String(id));
  e.dataTransfer.effectAllowed = 'move';
}

function onCalDragOver(e) {
  if (![...e.dataTransfer.types].includes('text/x-vs-task')) return;
  e.preventDefault();
  e.stopPropagation();
  e.currentTarget.classList.add('tk-drop');
}

function onCalDragLeave(e) { e.currentTarget.classList.remove('tk-drop'); }

async function onCalDrop(iso, e) {
  e.preventDefault();
  e.stopPropagation();
  e.currentTarget.classList.remove('tk-drop');
  const id = Number(e.dataTransfer.getData('text/x-vs-task'));
  const x = tkTask(id);
  if (!x || (x.due_date || '') === iso) return;
  const task = await tkPatch(id, { due_date: iso });
  if (task) { TK.report = null; renderTasks(); }
}

// --- The Summary panel: a period, its numbers, who did what, and a text the model writes ---
function tkPeriod() {
  const today = tkToday();
  if (TK.period === 'month') {
    const d = tkDate(today);
    return [tkIso(new Date(d.getFullYear(), d.getMonth(), 1)), tkIso(new Date(d.getFullYear(), d.getMonth() + 1, 0))];
  }
  if (TK.period === 'custom') {
    const a = TK.cFrom || tkAddDays(today, -14), b = TK.cTo || tkAddDays(today, 7);
    return a <= b ? [a, b] : [b, a];
  }
  const mon = tkMonday(today);
  return [mon, tkAddDays(mon, 6)];
}

function tkPeriodText(a, b) {
  const da = tkDate(a), db = tkDate(b);
  const opts = { day: 'numeric', month: 'long' };
  if (da.getMonth() === db.getMonth()) return `${da.getDate()}–${db.toLocaleDateString(tkLocale(), opts)}`;
  return `${da.toLocaleDateString(tkLocale(), opts)} – ${db.toLocaleDateString(tkLocale(), opts)}`;
}

function renderTasksReport() {
  const box = document.getElementById('tk-rep');
  box.classList.toggle('tk-rep-closed', !TK.rep);
  const ai = '<svg class="tb-ai" aria-hidden="true" viewBox="0 0 24 24"><path d="M10 2l1.9 5.6 5.6 1.9-5.6 1.9L10 17l-1.9-5.6-5.6-1.9 5.6-1.9z"/><path d="M18.5 13l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z"/></svg>';
  if (!TK.rep) {
    box.innerHTML = `<nav class="tk-rep-rail" aria-label="${escapeHtml(t('tkRepFolded'))}">
      <button class="tk-x" data-click="toggleTasksReport" title="${escapeHtml(t('tkRepOpen'))}" aria-label="${escapeHtml(t('tkRepOpen'))}">‹</button>
      <span class="tk-rail-sep"></span>
      <button class="tk-x" data-click="toggleTasksReport" title="${escapeHtml(t('tkRepTitle'))}" aria-label="${escapeHtml(t('tkRepTitle'))}">${ai}</button>
    </nav>`;
    return;
  }
  const [from, to] = tkPeriod();
  const scope = TK.data.tasks.filter(x => x.due_date && x.due_date >= from && x.due_date <= to
    && (!TK.people.length || TK.people.includes((x.who || '').toLocaleLowerCase())));
  const done = scope.filter(tkIsDone), over = scope.filter(tkIsOver), open = scope.filter(x => !tkIsDone(x) && !tkIsOver(x));
  const onTime = done.length + over.length ? Math.round((done.length / (done.length + over.length)) * 100) + '%' : '—';
  const who = [...new Set(scope.map(x => (x.who || '').toLocaleLowerCase()))].map(k => {
    const l = scope.filter(x => (x.who || '').toLocaleLowerCase() === k);
    const d = l.filter(tkIsDone).length, o = l.filter(tkIsOver).length;
    return { k, name: l[0].who || t('tkNobody'), kind: l[0].who_kind, n: l.length, d, o, w: l.length - d - o };
  }).sort((a, b) => b.n - a.n || b.o - a.o);
  const max = Math.max(1, ...who.map(r => r.n));
  const scopeText = TK.people.length ? (TK.people.length === 1 ? (tkPerson(TK.people[0])?.name || t('tkNobody')) : t('tkNPeople').replace('{n}', () => TK.people.length)) : t('tkAllPeopleLow');
  const chip = (on, fn, args, label) => `<button class="tk-chip${on ? ' tk-on' : ''}" aria-pressed="${on}" ${pageCall('click', fn, args)}>${escapeHtml(label)}</button>`;
  const periods = [['week', t('tkPWeek')], ['month', t('tkPMonth')], ['custom', t('tkPCustom')]];
  const incl = [['done', t('tkIDone')], ['open', t('tkIOpen')], ['over', t('tkIOver')]];
  const fmts = [['short', t('tkFmtShort')], ['people', t('tkFmtPeople')], ['mail', t('tkFmtMail')]];
  const rep = TK.report;
  const tone = { over: 'tk-red', done: 'tk-green' };
  box.innerHTML = `<div class="tk-rep-head">${ai}<b>${escapeHtml(t('tkRepTitle'))}</b><span class="tk-grow"></span>
      <button class="tk-x" data-click="toggleTasksReport" title="${escapeHtml(t('tkRepFold'))}" aria-label="${escapeHtml(t('tkRepFold'))}">›</button></div>
    <div class="tk-rep-body">
      <section class="tk-rsec"><div class="tk-cap">${escapeHtml(t('tkPeriodCap'))}</div>
        <div class="tk-wrap">${periods.map(([k, l]) => chip(TK.period === k, 'setTasksPeriod', [k], l)).join('')}</div>
        ${TK.period === 'custom' ? `<div class="tk-row2"><input type="date" class="tk-input tk-date" value="${from}" title="${escapeHtml(t('tkFromDate'))}" ${pageCall('change', 'setTasksCustom', ['from', { $: 'value' }])}>—
          <input type="date" class="tk-input tk-date" value="${to}" title="${escapeHtml(t('tkToDate'))}" ${pageCall('change', 'setTasksCustom', ['to', { $: 'value' }])}></div>` : ''}
        <span class="tk-dim tk-small">${escapeHtml(tkPeriodText(from, to))} · ${escapeHtml(scopeText)}</span></section>
      <section class="tk-kpis">
        ${[[done.length, t('tkKDone'), 'tk-green'], [open.length, t('tkKOpen'), 'tk-blue'], [over.length, t('tkKOver'), 'tk-red'], [onTime, t('tkKOnTime'), '']]
    .map(([v, l, c]) => `<div class="tk-kpi"><div class="tk-kpi-v ${c}">${escapeHtml(String(v))}</div><div class="tk-dim tk-small">${escapeHtml(l)}</div></div>`).join('')}
      </section>
      <section class="tk-rsec"><div class="tk-cap tk-legend">${escapeHtml(t('tkByPeopleCap'))}<span class="tk-grow"></span>
          <span><i class="tk-sw" style="background:#30D158"></i>${escapeHtml(t('tkLDone'))}</span><span><i class="tk-sw" style="background:#0A84FF"></i>${escapeHtml(t('tkLOpen'))}</span><span><i class="tk-sw" style="background:#FF6961"></i>${escapeHtml(t('tkLOver'))}</span></div>
        ${who.length ? who.map(r => `<button class="tk-bar-row${TK.people.length === 1 && TK.people[0] === r.k ? ' tk-on' : ''}" ${pageCall('click', 'pickTasksPerson', [r.k])}
            title="${escapeHtml(t('tkBarTitle').replace('{name}', () => r.name).replace('{d}', () => r.d).replace('{w}', () => r.w).replace('{o}', () => r.o))}">
            <span class="tk-ell">${escapeHtml(r.name)}</span>
            <span class="tk-bars"><span style="width:${(r.d / max) * 100}%;background:#30D158"></span><span style="width:${(r.w / max) * 100}%;background:#0A84FF"></span><span style="width:${(r.o / max) * 100}%;background:#FF6961"></span></span>
            <span class="tk-ratio">${r.d}/${r.n}</span></button>`).join('') : `<span class="tk-hint">${escapeHtml(t('tkNoTasksInPeriod'))}</span>`}
        ${who.length ? `<span class="tk-hint">${escapeHtml(t('tkBarHint'))}</span>` : ''}</section>
      <section class="tk-rsec"><div class="tk-cap">${escapeHtml(t('tkTextCap'))}</div>
        <div class="tk-wrap">${incl.map(([k, l]) => chip(TK.incl.includes(k), 'toggleTasksIncl', [k], (TK.incl.includes(k) ? '✓ ' : '') + l)).join('')}</div>
        <div class="tk-seg" role="radiogroup">${fmts.map(([k, l]) => `<button role="radio" aria-checked="${TK.fmt === k}" class="tk-seg-btn${TK.fmt === k ? ' tk-on' : ''}" ${pageCall('click', 'setTasksFmt', [k])}>${escapeHtml(l)}</button>`).join('')}</div>
        <button class="tk-btn tk-btn-primary tk-btn-wide" data-click="makeTasksReport" ${TK.reportBusy || !scope.length ? 'disabled' : ''}>${ai}${escapeHtml(rep ? t('tkRemake') : t('tkMake'))}</button>
        ${TK.reportBusy ? '<div class="tk-shim-box"><span class="tk-shim" style="width:90%"></span><span class="tk-shim" style="width:75%"></span><span class="tk-shim" style="width:82%"></span></div>' : ''}
        ${rep && !TK.reportBusy ? `<article class="tk-report"><b>${escapeHtml(rep.title)}</b>
            ${rep.lines.map(l => `<p class="${tone[l.tone] || ''}">${escapeHtml(l.text)}</p>`).join('')}
            <div class="tk-wrap tk-report-acts">
              <button class="tk-btn tk-btn-sm" data-click="copyTasksReport">${escapeHtml(TK.copied ? t('tkCopied') : t('tkCopy'))}</button>
              ${[['mail', t('tkSendMail'), t('tkSentMail')], ['obsidian', t('tkSendObs'), t('tkSentObs')], ['md', t('tkSendMd'), t('tkSentMd')]].map(([k, l, d]) =>
    `<button class="tk-btn tk-btn-sm${TK.sent.has(k) ? ' tk-sent' : ''}" ${pageCall('click', 'sendTasksReport', [k])}>${escapeHtml(TK.sent.has(k) ? '✓ ' + d : l)}</button>`).join('')}
            </div></article>` : ''}
        <span class="tk-hint">${escapeHtml(rep && rep.model ? t('tkMadeBy').replace('{model}', () => rep.model) : rep && rep.model_failed ? t('tkModelFailed') : t('tkMadeHint'))}</span>
      </section>
    </div>`;
}

function toggleTasksReport() { TK.rep = !TK.rep; if (TK.view === 'cal') TK.repList = null; renderTasks(); }
function setTasksPeriod(k) { TK.period = k; TK.report = null; renderTasks(); }
function setTasksCustom(which, value) {
  if (!value) return;
  if (which === 'from') TK.cFrom = value; else TK.cTo = value;
  TK.report = null;
  renderTasks();
}
function toggleTasksIncl(k) { TK.incl = TK.incl.includes(k) ? TK.incl.filter(x => x !== k) : TK.incl.concat(k); TK.report = null; renderTasks(); }
function setTasksFmt(k) { TK.fmt = k; TK.report = null; renderTasks(); }
function pickTasksPerson(k) {
  TK.people = TK.people.length === 1 && TK.people[0] === k ? [] : [k];
  TK.report = null;
  TK.keep.clear();
  renderTasks();
}

async function makeTasksReport() {
  const [start, end] = tkPeriod();
  TK.reportBusy = true;
  TK.report = null;
  TK.copied = false;
  TK.sent = new Set();
  renderTasksReport();
  const people = TK.people.map(k => tkPerson(k)?.name ?? k);
  const res = await fetch('/api/tasks/report', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start, end, include: TK.incl, fmt: TK.fmt, people, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
  }).catch(() => null);
  TK.reportBusy = false;
  if (!res || !res.ok) {
    const err = res ? await res.json().catch(() => ({})) : {};
    showToast(serverText(err.detail) || t('tkReportFailed'));
    renderTasksReport();
    return;
  }
  TK.report = await res.json();
  renderTasksReport();
}

async function copyTasksReport() {
  if (!TK.report) return;
  try { await navigator.clipboard.writeText(TK.report.text); } catch { showToast(t('tkCopyFailed')); return; }
  TK.copied = true;
  renderTasksReport();
}

async function sendTasksReport(kind) {
  const r = TK.report;
  if (!r) return;
  if (kind === 'mail') {
    openExternalMail(`mailto:?subject=${encodeURIComponent(r.title)}&body=${encodeURIComponent(r.text.slice(0, 1800))}`);
  } else {
    const res = await fetch('/api/tasks/report/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: r.text, title: r.title, target: kind === 'obsidian' ? 'obsidian' : 'md' }),
    });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(serverText(out.detail) || t('tkSaveFailed')); return; }
    if (out.cancelled) return;
  }
  TK.sent.add(kind);
  renderTasksReport();
}

// --- «+ Задача»: what to do; who and when, also read from the words; from which recording ---
const TKN = { text: '', who: '', kind: 'ext', other: '', due: null, rec: '', at: '', guess: null, guessTimer: null };

function openNewTask() {
  Object.assign(TKN, { text: '', who: tkMe() ? (tkPerson(tkMe())?.name || '') : '', kind: 'ext', other: '', due: tkAddDays(tkToday(), 1), rec: '', at: '', guess: null });
  if (TKN.who) TKN.kind = tkPerson(tkMe())?.kind || 'ext';
  document.getElementById('tk-n-text').value = '';
  document.getElementById('tk-n-other').value = '';
  document.getElementById('tk-n-at').value = '';
  const sel = document.getElementById('tk-n-rec');
  const recs = (state.meetings || []).filter(m => m.status === 'completed').slice(0, 40);
  sel.innerHTML = `<option value="">${escapeHtml(t('tkNoRecordOpt'))}</option>` + recs.map(m => `<option value="${m.id}">${escapeHtml(m.title)}</option>`).join('');
  if (state.activeMeeting && recs.some(m => m.id === state.activeMeeting.id)) sel.value = '';
  renderNewTask();
  document.getElementById('tk-new').classList.remove('hidden');
  setTimeout(() => document.getElementById('tk-n-text').focus(), 30);
}

function closeNewTask() { document.getElementById('tk-new').classList.add('hidden'); }
function closeNewTaskOnBackdrop(e) { if (e.target === e.currentTarget) closeNewTask(); }

function onNewTaskKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeNewTask(); }
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addNewTask(); }
}

function onNewTaskText(value) {
  TKN.text = value;
  TKN.guess = null;
  clearTimeout(TKN.guessTimer);
  TKN.guessTimer = setTimeout(guessNewTask, 450);
  renderNewTask();
}

async function guessNewTask() {
  if (!TKN.text.trim()) return;
  const res = await fetch('/api/tasks/guess', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: TKN.text }) }).catch(() => null);
  if (!res || !res.ok) return;
  const g = await res.json();
  if (!g.who && !g.due_date) return;
  if (g.who === TKN.who && (!g.due_date || g.due_date === TKN.due)) return;
  TKN.guess = g;
  renderNewTask();
}

function applyNewTaskGuess() {
  const g = TKN.guess;
  if (!g) return;
  if (g.who) { TKN.who = g.who; TKN.kind = g.who_kind || 'ext'; TKN.other = ''; document.getElementById('tk-n-other').value = ''; }
  if (g.due_date) TKN.due = g.due_date;
  TKN.guess = null;
  renderNewTask();
}

function onNewTaskOther(value) { TKN.other = value; renderNewTaskWho(); renderNewTaskFoot(); }
function onNewTaskDate(value) { if (value) { TKN.due = value; renderNewTask(); } }
function onNewTaskRec(value) { TKN.rec = value; renderNewTask(); }
function onNewTaskAt(value) { TKN.at = value; }
function pickNewTaskWho(name, kind) { TKN.who = name; TKN.kind = kind; TKN.other = ''; document.getElementById('tk-n-other').value = ''; renderNewTask(); }
function pickNewTaskDue(iso) { TKN.due = iso || null; renderNewTask(); }

function renderNewTask() {
  const g = TKN.guess, box = document.getElementById('tk-n-guess');
  if (g) {
    const bits = [g.who ? t('tkGuessWho').replace('{who}', () => g.who) : '', g.due_date ? t('tkGuessDue').replace('{due}', () => tkShortDate(g.due_date)) : ''].filter(Boolean);
    box.innerHTML = `<svg class="tb-ai" aria-hidden="true" viewBox="0 0 24 24"><path d="M10 2l1.9 5.6 5.6 1.9-5.6 1.9L10 17l-1.9-5.6-5.6-1.9 5.6-1.9z"/></svg>
      <span>${escapeHtml(t('tkGuess'))} ${escapeHtml(bits.join(' · '))}</span>
      <button class="tk-btn tk-btn-sm" data-click="applyNewTaskGuess">${escapeHtml(t('tkApply'))}</button>`;
  }
  box.classList.toggle('hidden', !g);
  renderNewTaskWho();
  const cur = TKN.due || '';
  document.getElementById('tk-n-due').innerHTML = tkDueChoices().map(([label, iso]) => {
    const on = cur === iso;
    return `<button type="button" class="tk-chip${on ? ' tk-on' : ''}" aria-pressed="${on}" ${pageCall('click', 'pickNewTaskDue', [iso])}>${escapeHtml(label)}${iso && ![tkToday(), tkAddDays(tkToday(), 1)].includes(iso) ? ` <span class="tk-chip-n">${escapeHtml(tkShortDate(iso))}</span>` : ''}</button>`;
  }).join('');
  document.getElementById('tk-n-date').value = cur;
  document.getElementById('tk-n-at').disabled = !TKN.rec;
  document.getElementById('tk-n-at').style.opacity = TKN.rec ? '1' : '0.4';
  renderNewTaskFoot();
}

function renderNewTaskWho() {
  const typed = TKN.other.trim();
  const frequent = TK.data.people.slice().sort((a, b) => tkIsMe(b.name) - tkIsMe(a.name) || b.open - a.open).slice(0, 8);
  const chips = frequent.map(p => {
    const on = !typed && TKN.who === p.name;
    return `<button type="button" class="tk-chip tk-chip-ava${on ? ' tk-on' : ''}" aria-pressed="${on}" ${pageCall('click', 'pickNewTaskWho', [p.name, p.kind])}>${tkAvatar(p.name, p.kind, 18)}${escapeHtml(p.name)}</button>`;
  });
  const nobodyOn = !typed && !TKN.who;
  chips.push(`<button type="button" class="tk-chip tk-chip-ava${nobodyOn ? ' tk-on' : ''}" aria-pressed="${nobodyOn}" ${pageCall('click', 'pickNewTaskWho', ['', 'none'])}>${tkAvatar('', 'none', 18)}${escapeHtml(t('tkNobody'))}</button>`);
  document.getElementById('tk-n-who').innerHTML = chips.join('');
  document.getElementById('tk-n-kinds').innerHTML = tkKindChips('tk-n-kinds', TKN.kind);
  document.getElementById('tk-n-kinds').classList.toggle('tk-faded', !typed);
}

function renderNewTaskFoot() {
  document.getElementById('tk-n-add').disabled = !TKN.text.trim();
}

function tkParseAt(text) {
  const p = (text || '').trim().split(':').map(Number);
  if (!p.length || p.some(n => Number.isNaN(n))) return null;
  return p.reduce((a, v) => a * 60 + v, 0);
}

async function addNewTask() {
  const what = TKN.text.trim();
  if (!what) return;
  const typed = TKN.other.trim();
  const body = {
    what, who: typed || TKN.who, who_kind: typed ? TKN.kind : (TKN.who ? TKN.kind : 'none'), due_date: TKN.due || '',
    meeting_id: Number(TKN.rec) || 0, at: TKN.rec ? tkParseAt(TKN.at) : null,
  };
  const res = await fetch('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(serverText(out.detail) || t('tkSaveFailed')); return; }
  closeNewTask();
  TK.keep.add(out.id);
  TK.report = null;
  await tkRefreshPeople();
  tkNote(t('tkAdded'));
}

// The list of recordings changed (a recording analysed, renamed, deleted): the tasks follow, unless
// the user is in the middle of something here (a menu, a name being typed, the words of a task)
async function refreshTasksPage() {
  const busy = document.getElementById('tk-pop') || !document.getElementById('tk-new').classList.contains('hidden')
    || document.activeElement?.closest?.('#tk-list, #tk-cal, #tk-rep');
  if (busy) return;
  await renderTasksPage();
}

registerView('tasks', { show: renderTasksPage, refresh: (changed) => { if (changed) refreshTasksPage(); }, hide: () => { tkClosePop(); closeNewTask(); } });
