// VerbaScribe page script: the shell. The top bar (record at once, the parts of the app, the open
// recording), the views of the main area, the status bar. docs/redesign-plan.md tells how a view plugs in.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// --- Views of the main area ---
// "library" is the open recording (its transcript, analysis and speakers); its name stays for the
// many places that open a recording. Ctrl+1…5 go through the first five, in the order of the top bar.
const APP_VIEWS = ['home', 'library', 'tasks', 'studio', 'ask', 'settings'];
const VIEW_HOOKS = {};

// A view of a package: show() runs each time it comes on screen, hide() when another one does,
// refresh() when the list of recordings changed while it is on screen
function registerView(name, hooks) {
  VIEW_HOOKS[name] = hooks || {};
}

// What the views refresh for: a recording added, removed, renamed, finished or analysed — not the
// progress of one being processed, which the list is polled for every 2 s
function librarySignature() {
  return (state.meetings || []).map(m => `${m.id}:${m.status}:${m.title || ''}:${m.completed_at || ''}:${m.has_summary ? 1 : 0}`).join('|');
}

// The list of recordings was loaded again (loadMeetingsList): the bar and the view on screen follow;
// refresh(changed) says whether anything but the progress changed
function onLibraryChanged() {
  renderTopbar();
  refreshTasksCount();
  const sig = librarySignature(), changed = sig !== state.librarySig;
  state.librarySig = sig;
  VIEW_HOOKS[state.appView]?.refresh?.(changed);
}

function switchAppView(view) {
  if (!APP_VIEWS.includes(view)) view = 'library';
  const prev = state.appView;
  if (state.theatre && view !== 'library') exitTheatre();
  if (prev && prev !== view) {
    if (prev !== 'settings') state.prevView = prev;  // where the settings page goes back to
    if (prev === 'settings') leaveSettingsPage();
    VIEW_HOOKS[prev]?.hide?.();
  }
  state.appView = view;
  // The sidebar always shows the library; the main area shows the chosen part
  for (const v of APP_VIEWS) document.getElementById(`${v}-view`)?.classList.toggle('hidden', v !== view);
  if (view === 'ask') loadPeople();
  hideContextMenu();
  hideFolderMenu();
  if (view === 'studio') {
    prepareStudio();
    syncStudioCaptions();
  } else if (studio.focus) {
    toggleStudioFocus(false);
  }
  if (view === 'ask') {
    renderAskScope();
    renderAskThread();
    setTimeout(() => document.getElementById('library-question').focus(), 30);
  }
  // Reached without openModal (another script switching to it): the page needs its content
  if (view === 'settings' && document.getElementById('modal-settings')?.classList.contains('hidden')) openModal('settings');
  VIEW_HOOKS[view]?.show?.();
  updateRecordButtonState();
  renderTopbar();
}

// The tabs of the open recording, in the top bar: transcript | summary (analysis) | speakers | info
function openRecordTab(tab) {
  if (!state.activeMeeting) { switchAppView('home'); return; }
  switchAppView('library');
  switchWorkspaceTab(tab);
}

// --- The top bar ---
function renderTopbar() {
  const view = state.appView || 'home';
  const tab = state.activeWorkspaceTab || 'transcript';
  const set = (id, on) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('tb-active', on);
    if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  };
  set('nav-home', view === 'home');
  set('nav-tasks', view === 'tasks');
  set('nav-studio', view === 'studio');
  set('nav-ask', view === 'ask');
  const m = state.activeMeeting;
  document.getElementById('tb-record')?.classList.toggle('hidden', !m);
  set('nav-transcript', view === 'library' && tab === 'transcript');
  set('nav-summary', view === 'library' && tab === 'summary');
  set('nav-speakers', view === 'library' && tab === 'speakers');
  if (m) {
    const name = document.getElementById('tb-recname');
    if (name.textContent !== m.title) name.textContent = m.title;
    name.title = t('tbOpenRecord').replace('{title}', () => m.title);
    // the sections made (summaries["error"] is the last failure, not a section)
    const ready = Object.entries(m.summaries || {}).filter(([k, v]) => k !== 'error' && typeof v === 'string' && v.trim()).length;
    badge('nav-summary-n', ready);
    badge('nav-speakers-n', Object.keys(m.speakers || {}).length);
  }
}

function badge(id, n) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = n ? String(n) : '';
  el.classList.toggle('hidden', !n);
}

// The open tasks of the whole library, on the "Tasks" tab (refreshed with the list, at most every 10 s)
let _tasksCountAt = 0;
async function refreshTasksCount(force) {
  if (!force && Date.now() - _tasksCountAt < 10000) return;
  _tasksCountAt = Date.now();
  const items = await fetch('/api/commitments').then(r => (r.ok ? r.json() : null)).catch(() => null);
  if (Array.isArray(items)) badge('nav-tasks-n', items.length);
}

// The open recording changes in many places; the bar follows it without each of them calling it
setInterval(() => { if (!document.hidden) renderTopbar(); }, 1500);

// The ⋯ of the open recording: its details, rename, move, show the file, delete
function openRecordBarMenu(e) {
  e.stopPropagation();
  const m = state.activeMeeting;
  if (!m) return;
  const r = e.currentTarget.getBoundingClientRect();
  const at = (fn) => () => { state.contextMeetingId = m.id; fn(); };
  openTextMenuAt([
    { label: t('tbRecInfo'), key: 'Ctrl+I', run: () => rpOpenInfo() },
    { label: t('ctxRename'), key: 'F2', run: at(ctxRenameRecording) },
    { label: t('tbRecMove'), run: () => openMoveRecordMenu(m, r) },
    { label: t('ctxReveal'), run: at(ctxRevealInExplorer) },
    { sep: true },
    { label: t('tbRecDelete'), key: 'Del', run: at(ctxDeleteRecording) },
  ], r.right - 230, r.bottom + 4);
}

function openMoveRecordMenu(m, r) {
  const folders = [...(state.folders || [])].sort((a, b) => folderName(a).localeCompare(folderName(b), state.uiLang));
  openTextMenuAt(folders.map(f => ({
    label: folderName(f), key: (m.folder_id ?? null) === f.id ? '✓' : '',
    run: () => moveMeetingToFolder(m.id, f.id),
  })), r.right - 230, r.bottom + 4);
}

// --- Record at once: the red button of the top bar, with the last studio settings, in any view ---
let _quickRecResult = null;
let _quickRecHideTimer = null;

let _quickRecBusy = false;  // starting or saving: a second press waits for it

async function shellQuickRecord() {
  if (_quickRecBusy) return;
  _quickRecBusy = true;
  hideQuickRecDone();
  try {
    if (isRecording()) {
      const clock = document.getElementById('studio-timer').textContent;
      const first = await stopAndTranscribeRecording({ quiet: true });
      if (first !== undefined) showQuickRecDone(first, clock);
      return;
    }
    state.quickRecStay = true;  // startLiveRecording leaves the view as it is
    await prepareStudio(state.recPresetId || defaultRecordPresetId());
    await startLiveRecording();
  } finally {
    state.quickRecStay = false;
    _quickRecBusy = false;
    updateRecordButtonState();
  }
}

function showQuickRecDone(first, clock) {
  _quickRecResult = first || null;
  document.getElementById('tb-rec-saved-time').textContent = clock || '';
  document.getElementById('tb-rec-open').classList.toggle('hidden', !first);
  document.getElementById('tb-rec-done').classList.remove('hidden');
  clearTimeout(_quickRecHideTimer);
  _quickRecHideTimer = setTimeout(hideQuickRecDone, 9000);
}

function hideQuickRecDone() {
  clearTimeout(_quickRecHideTimer);
  document.getElementById('tb-rec-done')?.classList.add('hidden');
}

function openQuickRecResult() {
  hideQuickRecDone();
  if (_quickRecResult) openMeeting(_quickRecResult.id);
}

// The red button and the studio tab while recording: the time on both (called every 400 ms)
function updateRecordButtonState() {
  const btn = document.getElementById('tb-rec');
  if (!btn) return;
  const recording = isRecording();
  const timer = document.getElementById('studio-timer').textContent;
  btn.classList.toggle('tb-rec-on', recording);
  document.getElementById('tb-rec-label').textContent = recording ? t('tbRecStop') : t('tbRecord');
  const clock = document.getElementById('tb-rec-clock');
  clock.textContent = recording ? timer : '';
  clock.classList.toggle('hidden', !recording);
  btn.title = recording ? t('tipTbRecordStop') : t('tipTbRecord');
  document.getElementById('nav-studio-dot').classList.toggle('hidden', !recording);
  document.getElementById('nav-studio-label').textContent = recording ? `${t('navStudio')} · ${timer}` : t('navStudio');
}

// --- The settings: a page of the main area. The settings window moves into it the first time ---
function mountSettingsPage() {
  const modal = document.getElementById('modal-settings');
  const view = document.getElementById('settings-view');
  if (!modal || !view || modal.parentElement === view) return;
  view.appendChild(modal);
  modal.classList.remove('fixed', 'inset-0', 'modal-backdrop', 'z-50', 'p-4', 'items-center', 'justify-center');
  modal.classList.add('settings-page');
  modal.removeAttribute('data-click');  // no backdrop to click: it is the page
}

function leaveSettingsPage() {
  const modal = document.getElementById('modal-settings');
  if (!modal || modal.classList.contains('hidden')) return;
  modal.classList.add('hidden');
  stopSettingsAudioTest();
}

// --- The status bar: the load of the computer and the queue ---
function renderStatusbar() {
  const text = (id) => (document.getElementById(id)?.textContent || '').trim();
  const put = (id, v) => { const el = document.getElementById(id); if (el && el.textContent !== v) el.textContent = v || '—'; };
  put('sb-cpu', text('cpu-text'));
  put('sb-ram', text('ram-text'));
  const gpu = text('vram-text');
  put('sb-gpu', gpu ? `${gpu} · ${text('hw-text')}` : text('hw-text'));
  const items = state.queue || [];
  const q = document.getElementById('sb-queue');
  if (!q) return;
  const run = items.find(x => x.state === 'running');
  if (!items.length) {
    q.textContent = t('sbQueueEmpty');
    q.classList.remove('sb-queue-busy');
    return;
  }
  const m = run ? (state.meetings.find(x => x.id === run.id) || {}) : {};
  const pct = run ? (m.progress ?? run.progress ?? 0) : 0;
  const more = items.length - (run ? 1 : 0);
  q.textContent = run
    ? `${queueKindLabel(run)} · ${pct}% — «${run.title}»${more ? ` · ${t('sbQueueMore').replace('{n}', () => more)}` : ''}`
    : t('sbQueueWaiting').replace('{n}', () => items.length);
  q.classList.add('sb-queue-busy');
}

function openStatusbarQueue() {
  const run = (state.queue || []).find(x => x.state === 'running') || (state.queue || [])[0];
  if (run) openMeeting(run.id);
}
setInterval(() => { if (!document.hidden) renderStatusbar(); }, 2000);

// The first view: the home page
document.addEventListener('DOMContentLoaded', () => {
  if (!state.appView) switchAppView('home');
  refreshTasksCount(true);
  renderStatusbar();
});
