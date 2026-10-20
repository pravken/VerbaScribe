// VerbaScribe page script, part 4 of 9: Library: the list, live updates, the queue, app views, the assistant, folders, views and sorting, dropped files.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// --- Meetings List, Global Search & Sidebar ---
async function loadMeetingsList() {
  const [res] = await Promise.all([fetch('/api/meetings'), loadFolders()]);
  state.meetings = await res.json();
  if (!state.globalSearchQuery) {
    renderMeetingsSidebar();
  }
  if (state.appView === 'ask') renderAskScope();

  if (state.activeMeeting) {
    const updated = state.meetings.find(m => m.id === state.activeMeeting.id);
    if (updated && updated.title !== state.activeMeeting.title) {
      // Renamed elsewhere (e.g. a title made from the content after processing)
      state.activeMeeting.title = updated.title;
      const input = document.getElementById('meeting-title-input');
      if (document.activeElement !== input) input.value = updated.title;
    }
    if (updated && updated.status !== state.activeMeeting.status) {
      // Segments and speakers only change when a processing stage finishes
      const finished = updated.status === 'completed';
      await selectMeeting(updated.id, false);
      if (finished) buildPlayerWaveformEnvelope(state.activeMeeting);
    } else if (updated && (updated.progress !== state.activeMeeting.progress || updated.stage_text !== state.activeMeeting.stage_text)) {
      state.activeMeeting.progress = updated.progress;
      state.activeMeeting.stage_text = updated.stage_text;
      updateProcessingBanner(state.activeMeeting);
    }
  } else if (state.meetings.length > 0 && !state.activeMeeting) {
    await selectMeeting(state.meetings[0].id, true);
  }
}

// --- Live updates ---
// The server pushes progress over Server-Sent Events (/api/events). Polling stays as a safety
// net: every 30 s while the stream is connected, at the old 2 s / 8 s cadence when it is not.
const live = { connected: false, greeted: false, reloadTimer: null, renderQueued: false };
const LIVE_PROGRESS_FIELDS = ['status', 'progress', 'stage_text', 'error_message', 'duration'];

function startLiveUpdates() {
  if (!window.EventSource) return;
  const es = new EventSource('/api/events');
  es.onopen = () => { live.connected = true; };
  // EventSource reconnects by itself (server sends retry: 3000)
  es.onerror = () => { live.connected = false; };
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    handleLiveEvent(ev);
  };
}

function scheduleListReload() {
  clearTimeout(live.reloadTimer);
  live.reloadTimer = setTimeout(() => loadMeetingsList().catch(console.error), 150);
}

function scheduleSidebarRender() {
  if (live.renderQueued) return;
  live.renderQueued = true;
  requestAnimationFrame(() => {
    live.renderQueued = false;
    if (!state.globalSearchQuery) renderMeetingsSidebar();
  });
}

// --- Processing time: elapsed and a rough estimate of the remaining time ---
function formatDurationShort(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

// Stages of a job run at very different speeds (e.g. translation vs. speech synthesis), so the
// remaining time is estimated from the progress rate of the last few minutes, not since the start.
const _progressHistory = {};
const ETA_WINDOW_SEC = 240;

function jobTiming(q, pct) {
  if (!q || !q.started_at) return null;
  const now = Date.now() / 1000;
  const elapsed = Math.max(0, now - q.started_at);
  let hist = _progressHistory[q.id];
  if (!hist || hist.started !== q.started_at) hist = _progressHistory[q.id] = { started: q.started_at, samples: [] };
  const samples = hist.samples;
  if (!samples.length || samples[samples.length - 1].p !== pct) samples.push({ t: now, p: pct });
  while (samples.length > 2 && now - samples[1].t > ETA_WINDOW_SEC) samples.shift();

  let remaining = null;
  const first = samples[0];
  const span = now - first.t;  // includes time without progress, so a stall raises the estimate
  if (samples.length >= 2 && pct > first.p && span >= 30 && pct < 100) {
    remaining = (100 - pct) * span / (pct - first.p);
  } else if (pct >= 5 && pct < 100 && elapsed >= 10) {
    remaining = elapsed * (100 - pct) / pct;
  }
  return { elapsed, remaining };
}

function formatTiming(timing) {
  if (!timing) return '';
  let text = formatDurationShort(timing.elapsed);
  if (timing.remaining != null) {
    const mins = Math.round(timing.remaining / 60);
    text += ' · ' + (mins < 1 ? t('etaUnderMinute') : t('etaMinutes').replace('{n}', mins));
  }
  return text;
}

// Re-render the elapsed time every second while something is running
setInterval(() => {
  if (!(state.queue || []).some(q => q.state === 'running')) return;
  renderQueuePanel();
  if (state.activeMeeting) updateProcessingBanner(state.activeMeeting);
}, 1000);

// --- Processing queue panel ---
function scheduleQueueReload() {
  clearTimeout(live.queueTimer);
  live.queueTimer = setTimeout(() => loadQueue().catch(console.error), 120);
}

async function loadQueue() {
  const res = await fetch('/api/queue');
  if (!res.ok) return;
  state.queue = await res.json();
  renderQueuePanel();
}

function queueKindLabel(q) {
  const lang = (q.lang || '').toUpperCase();
  if (q.kind === 'dub') return `${t('queueKindDub')} → ${lang}`;
  if (q.kind === 'translate') return `${t('queueKindTranslate')} → ${lang}`;
  if (q.kind === 'burn_subs') return `${t('queueKindBurnSubs')} (${lang})`;
  return t('queueKindTranscribe');
}

function renderQueuePanel() {
  const panel = document.getElementById('queue-panel');
  if (!panel) return;
  const items = state.queue || [];
  panel.classList.toggle('hidden', items.length === 0);
  document.getElementById('queue-count').textContent = items.length ? `(${items.length})` : '';
  document.getElementById('queue-list').innerHTML = items.map(q => {
    // Live progress comes from SSE updates of the library list
    const m = state.meetings.find(x => x.id === q.id) || {};
    const running = q.state === 'running';
    const pct = running ? (m.progress ?? q.progress ?? 0) : 0;
    const detail = q.cancelling
      ? t('queueCancelling')
      : running ? (m.stage_text || q.stage_text || '') : t('queueWaiting').replace('{n}', q.position);
    const timing = running && !q.cancelling ? formatTiming(jobTiming(q, pct)) : '';
    return `
      <div onclick="openMeeting(${q.id}, true)" title="${escapeHtml(detail)}"
        class="group px-2 py-1.5 rounded-md cursor-pointer hover:bg-white/5 ${running ? 'bg-apple-blue/10' : ''}">
        <div class="flex items-center justify-between gap-2">
          <span class="flex items-center gap-1.5 min-w-0">
            ${running && !q.cancelling ? '<span class="live-dot"></span>' : ''}
            <span class="text-[12px] font-medium text-white truncate">${escapeHtml(q.title)}</span>
          </span>
          <button onclick="event.stopPropagation(); cancelProcessing(${q.id})" title="${escapeHtml(t('tipQueueCancel'))}"
            class="shrink-0 text-apple-tertiary hover:text-apple-red opacity-60 group-hover:opacity-100 transition ${q.cancelling ? 'hidden' : ''}">${SVG_ICONS.closeX}</button>
        </div>
        <div class="flex items-center justify-between gap-2 text-[10.5px] mt-0.5">
          <span class="${q.kind === 'transcribe' ? 'text-apple-secondary' : 'text-apple-amber'} shrink-0">${escapeHtml(queueKindLabel(q))}</span>
          <span class="text-apple-tertiary truncate font-mono">${running && !q.cancelling ? `${escapeHtml(timing)}${timing ? ' · ' : ''}${pct}%` : escapeHtml(detail)}</span>
        </div>
        ${running ? `<div class="w-full h-1 bg-white/10 rounded-full overflow-hidden mt-1"><div class="h-full bg-apple-blue progress-live transition-all duration-300" style="width:${Math.max(3, pct)}%"></div></div>` : ''}
      </div>`;
  }).join('');
}

function handleLiveEvent(ev) {
  if (ev.type === 'hello') {
    // After a reconnect (e.g. server restart) changes may have been missed
    if (live.greeted) scheduleListReload();
    live.greeted = true;
    return;
  }
  if (ev.type === 'list') { scheduleListReload(); return; }
  if (ev.type === 'queue') { scheduleQueueReload(); scheduleVramRefresh(800); return; }
  if (ev.type === 'exported') { onExportedEvent(ev); scheduleListReload(); return; }
  if (ev.type === 'call') { handleCallEvent(ev); return; }
  if (ev.type !== 'progress') return;

  const m = state.meetings.find(x => x.id === ev.id);
  if (!m) { scheduleListReload(); return; }
  for (const f of LIVE_PROGRESS_FIELDS) if (f in ev) m[f] = ev[f];
  if ((state.queue || []).some(q => q.id === ev.id)) renderQueuePanel();
  scheduleSidebarRender();

  const active = state.activeMeeting;
  if (!active || active.id !== ev.id) return;
  if (ev.status && ev.status !== active.status) {
    // Segments and speakers only change when a processing stage finishes
    selectMeeting(ev.id, false).then(() => {
      if (ev.status === 'completed') buildPlayerWaveformEnvelope(state.activeMeeting);
    });
  } else {
    active.progress = m.progress;
    active.stage_text = m.stage_text;
    updateProcessingBanner(active);
  }
}

async function pollMeetingsLoop() {
  try {
    await loadMeetingsList();
    await loadQueue();
  } catch (e) {
    console.error(e);
  }
  const busy = state.meetings.some(m => !DONE_STATUSES.includes(m.status));
  setTimeout(pollMeetingsLoop, live.connected ? 30000 : (busy ? 2000 : 8000));
}

async function handleGlobalSidebarSearch(rawVal) {
  const q = (rawVal || '').trim();
  state.globalSearchQuery = q;
  if (!q) {
    state.globalSearchResults = null;
    renderMeetingsSidebar();
    return;
  }

  clearTimeout(window._globalSearchDebounce);
  window._globalSearchDebounce = setTimeout(async () => {
    const res = await fetch(`/api/meetings/search?q=${encodeURIComponent(q)}`);
    state.globalSearchResults = await res.json();
    renderMeetingsSidebar();
  }, 180);
}

// --- App parts: library, recording studio, questions ---
const APP_VIEWS = ['library', 'studio', 'ask'];
state.appView = 'library';

function switchAppView(view) {
  if (!APP_VIEWS.includes(view)) view = 'library';
  if (state.theatre && view !== 'library') exitTheatre();
  state.appView = view;
  // The sidebar always shows the library; the main area shows the chosen part
  for (const v of APP_VIEWS) {
    document.getElementById(`${v}-view`).classList.toggle('hidden', v !== view);
    const tab = document.getElementById(`nav-${v}`);
    tab.classList.toggle('app-tab-active', v === view);
    tab.setAttribute('aria-selected', String(v === view));
  }
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
  updateRecordButtonState();
}

// --- Questions: about one recording, a folder (with its subfolders) or the whole library ---
const SVG_SPARK = '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>';
state.askScope = { type: 'all' };
const askThread = [];
let _askSeq = 0;

function openLibraryAsk(question, scope) {
  if (scope) state.askScope = scope;
  switchAppView('ask');
  const input = document.getElementById('library-question');
  if (question) input.value = question;
  input.focus();
}

function folderById(id) {
  return (state.folders || []).find(f => f.id === id) || null;
}

function folderName(f) {
  if (!f) return '';
  return f.system_key ? t(`folderSystem_${f.system_key}`) : f.name;
}

// Ids of a folder and all folders inside it
function folderSubtreeIds(id) {
  const out = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const f of state.folders || []) {
      if (f.parent_id != null && out.has(f.parent_id) && !out.has(f.id)) { out.add(f.id); grew = true; }
    }
  }
  return out;
}

function meetingsInFolderTree(id) {
  const ids = folderSubtreeIds(id);
  return state.meetings.filter(m => ids.has(m.folder_id));
}

function askScopeValid(scope) {
  if (scope.type === 'folder') return !!folderById(scope.id);
  if (scope.type === 'person') return !!personByName(scope.name);
  if (scope.type === 'meeting') return state.meetings.some(m => m.id === scope.id);
  return true;
}

function askScopeLabel(scope) {
  if (scope.type === 'folder') {
    const f = folderById(scope.id);
    return `${t('askScopeFolder')}: ${folderName(f)} · ${t('askRecordingsCount').replace('{n}', meetingsInFolderTree(scope.id).length)}`;
  }
  if (scope.type === 'meeting') {
    const m = state.meetings.find(x => x.id === scope.id);
    return `${t('askScopeMeeting')}: ${m ? m.title : ''}`;
  }
  if (scope.type === 'person') {
    const p = personByName(scope.name);
    return `${t('askScopePerson').replace('{name}', scope.name)} · ${t('askRecordingsCount').replace('{n}', p ? p.recordings.length : 0)}`;
  }
  return `${t('askScopeAll')} · ${t('askRecordingsCount').replace('{n}', state.meetings.length)}`;
}

function renderAskScope() {
  if (!askScopeValid(state.askScope)) state.askScope = { type: 'all' };
  const box = document.getElementById('ask-scope-list');
  const sc = state.askScope;
  const item = (scope, label, count, depth, icon) => {
    const on = scope.type === sc.type && scope.id === sc.id;
    return `<button onclick='setAskScope(${JSON.stringify(scope)})'
      class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition ${on ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'}"
      style="padding-left:${8 + depth * 14}px">
      <span class="shrink-0 ${on ? 'text-white' : 'text-apple-tertiary'}">${icon}</span>
      <span class="truncate flex-1">${escapeHtml(label)}</span>
      ${count != null ? `<span class="text-[10.5px] font-mono ${on ? 'text-white/80' : 'text-apple-tertiary'}">${count}</span>` : ''}
    </button>`;
  };
  let html = item({ type: 'all' }, t('askScopeAll'), state.meetings.length, 0, ICON_LIBRARY);
  const walk = (parentId, depth) => {
    for (const f of sortedFolders(parentId)) {
      html += item({ type: 'folder', id: f.id }, folderName(f), meetingsInFolderTree(f.id).length, depth, folderIcon(f));
      walk(f.id, depth + 1);
    }
  };
  walk(null, 0);
  const m = state.activeMeeting;
  if (m) {
    html += '<div class="h-px bg-white/10 my-1.5"></div>'
      + item({ type: 'meeting', id: m.id }, `${t('askScopeMeeting')}: ${m.title}`, null, 0, ICON_FILE);
  }
  box.innerHTML = html;
  document.getElementById('ask-scope-label').textContent = askScopeLabel(sc);
  renderPeople();
}

// --- People: everyone named in the library, and the recordings they speak in ---
state.people = [];

function personByName(name) {
  return (state.people || []).find(p => p.name.toLowerCase() === String(name || '').toLowerCase()) || null;
}

async function loadPeople() {
  state.people = await fetch('/api/people').then(r => r.ok ? r.json() : []).catch(() => []);
  if (state.appView === 'ask') renderAskScope();
}

function formatSpeakingTime(sec) {
  const min = Math.round(sec / 60);
  return min >= 60 ? `${Math.floor(min / 60)} ${t('unitH')} ${min % 60} ${t('unitMin')}` : `${Math.max(1, min)} ${t('unitMin')}`;
}

function renderPeople() {
  const block = document.getElementById('ask-people-block');
  const people = state.people || [];
  block.classList.toggle('hidden', !people.length);
  if (!people.length) return;
  const sc = state.askScope;
  document.getElementById('ask-people-list').innerHTML = people.slice(0, 50).map(p => {
    const on = sc.type === 'person' && sc.name.toLowerCase() === p.name.toLowerCase();
    const tip = t('tipPerson').replace('{n}', p.recordings.length).replace('{time}', formatSpeakingTime(p.total_seconds))
      + '\n' + p.recordings.slice(0, 12).map(r => `${r.date} · ${r.title} · ${formatSpeakingTime(r.seconds)}`).join('\n');
    return `<button onclick='setAskScope(${JSON.stringify({ type: 'person', name: p.name })})' title="${escapeHtml(tip)}"
      class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] transition ${on ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'}">
      <span class="shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold ${on ? 'bg-white/25' : 'bg-white/10 text-apple-secondary'}">${escapeHtml(p.name.slice(0, 1).toUpperCase())}</span>
      <span class="truncate flex-1">${escapeHtml(p.name)}${p.profile ? ` <span class="${on ? 'text-white/70' : 'text-apple-tertiary'}" title="${escapeHtml(t('tipPersonVoice'))}">◉</span>` : ''}</span>
      <span class="text-[10.5px] font-mono ${on ? 'text-white/80' : 'text-apple-tertiary'}">${p.recordings.length}</span>
    </button>`;
  }).join('');
}

function setAskScope(scope) {
  state.askScope = scope;
  renderAskScope();
  document.getElementById('library-question').focus();
}

function askScopePayload(scope) {
  if (scope.type === 'folder') return { folder_id: scope.id };
  if (scope.type === 'person') return { meeting_ids: (personByName(scope.name) || { recordings: [] }).recordings.map(r => r.meeting_id) };
  if (scope.type === 'meeting') return { meeting_ids: [scope.id] };
  return {};
}

// --- Digest: one summary of a folder or a period, shown in the thread like an answer ---
function openDigestMenu(e) {
  e.stopPropagation();
  document.getElementById('digest-menu')?.remove();
  const scope = state.askScope;
  const menu = document.createElement('div');
  menu.id = 'digest-menu';
  menu.className = 'fixed w-56 macos-menu z-50';
  const where = scope.type === 'folder' ? t('digestScopeFolder') : t('digestScopeAll');
  menu.innerHTML = `<div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(where)}</div>`
    + [[7, t('digestWeek')], [30, t('digestMonth')], [0, t('digestEverything')]]
      .map(([d, label]) => `<button class="macos-menu-item" data-days="${d}"><span>${escapeHtml(label)}</span></button>`).join('');
  menu.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-days]');
    ev.stopPropagation();
    if (!b) return;
    menu.remove();
    makeDigest(Number(b.dataset.days) || null);
  });
  document.body.appendChild(menu);
  const r = e.currentTarget.getBoundingClientRect();
  menu.style.left = `${Math.min(r.left, window.innerWidth - 232)}px`;
  menu.style.top = `${r.bottom + 4}px`;
}

document.addEventListener('click', () => document.getElementById('digest-menu')?.remove());

async function makeDigest(days) {
  const scope = state.askScope.type === 'folder' ? { ...state.askScope } : { type: 'all' };
  const period = days === 7 ? t('digestWeek') : days === 30 ? t('digestMonth') : t('digestEverything');
  const item = { id: ++_askSeq, question: `${t('digestBtn')} · ${period}`, scopeLabel: askScopeLabel(scope), status: 'pending', data: null, error: '' };
  askThread.unshift(item);
  renderAskThread();
  try {
    const res = await fetch('/api/library/digest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_id: scope.type === 'folder' ? scope.id : null, days: days || null, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Error');
    item.data = data;
    item.status = 'done';
  } catch (e) {
    item.error = e.message;
    item.status = 'error';
  }
  renderAskThread();
}

async function askLibrary() {
  const input = document.getElementById('library-question');
  const question = input.value.trim();
  if (!question) return;
  const btn = document.getElementById('library-ask-btn');
  const scope = { ...state.askScope };
  const item = { id: ++_askSeq, question, scopeLabel: askScopeLabel(scope), status: 'pending', data: null, error: '' };
  askThread.unshift(item);
  input.value = '';
  btn.disabled = true;
  renderAskThread();
  try {
    const res = await fetch('/api/library/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, lang: state.uiLang === 'en' ? 'en' : 'ru', ...askScopePayload(scope) }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Error');
    item.data = data;
    item.status = 'done';
  } catch (e) {
    item.error = e.message;
    item.status = 'error';
  } finally {
    btn.disabled = false;
    renderAskThread();
  }
}

function renderAskThread() {
  const box = document.getElementById('ask-thread');
  if (!askThread.length) {
    box.innerHTML = `<div class="py-16 text-center text-xs text-apple-tertiary">${escapeHtml(t('askEmpty'))}</div>`;
    return;
  }
  box.innerHTML = askThread.map(item => `
    <div class="rounded-2xl border apple-border bg-apple-sidebar/60 overflow-hidden" data-ask="${item.id}">
      <div class="px-5 py-3 border-b apple-border flex items-start justify-between gap-3">
        <div class="text-[13px] font-semibold text-white min-w-0">${escapeHtml(item.question)}</div>
        <div class="text-[10.5px] text-apple-tertiary shrink-0 max-w-[45%] truncate" title="${escapeHtml(item.scopeLabel)}">${escapeHtml(item.scopeLabel)}</div>
      </div>
      <div class="px-5 py-4 ask-body"></div>
    </div>`).join('');
  for (const item of askThread) {
    const body = box.querySelector(`[data-ask="${item.id}"] .ask-body`);
    if (item.status === 'pending') {
      body.innerHTML = `<div class="py-4 text-center text-xs text-apple-secondary animate-pulse">${escapeHtml(t('libraryAskWorking'))}</div>`;
    } else if (item.status === 'error') {
      body.innerHTML = `<div class="text-xs text-apple-red">${escapeHtml(item.error)}</div>`;
    } else {
      renderLibraryAnswer(body, item);
    }
  }
}

function renderLibraryAnswer(body, item) {
  const data = item.data || {};
  if (!data.found) {
    body.innerHTML = `<div class="text-xs text-apple-secondary">${escapeHtml(t('libraryAskNothing'))}</div>`;
    return;
  }
  const byN = Object.fromEntries((data.sources || []).map(s => [s.n, s]));
  let html = sanitizedMarkdown(data.answer || '');
  html = html.replace(/\[(\d{1,3})\]/g, (m, n) => byN[n]
    ? `<button class="lib-cite" onclick="openLibrarySource(${item.id}, ${Number(n)})" title="${escapeHtml(byN[n].title)} · ${formatTime(byN[n].start)}">${n}</button>`
    : m);
  // Only the fragments the answer refers to; all of them if it cites none
  const cited = new Set([...(data.answer || '').matchAll(/\[(\d{1,3})\]/g)].map(x => Number(x[1])));
  const shown = (data.sources || []).filter(s => !cited.size || cited.has(s.n)).slice(0, 20);
  const sources = shown.map(s => `
    <button onclick="openLibrarySource(${item.id}, ${s.n})" class="w-full text-left px-2.5 py-1.5 rounded-md hover:bg-white/5 transition">
      <div class="flex items-center gap-2 text-[11px] min-w-0">
        <span class="lib-cite">${s.n}</span>
        <span class="text-white truncate">${escapeHtml(s.title)}</span>
        <span class="text-apple-tertiary font-mono shrink-0 ml-auto">${escapeHtml(s.date)} · ${formatTime(s.start)}</span>
      </div>
      <div class="text-[11px] text-apple-secondary truncate mt-0.5 pl-7">${s.speaker ? escapeHtml(s.speaker) + ': ' : ''}${escapeHtml(s.text)}</div>
    </button>`).join('');
  body.innerHTML = `
    <div class="markdown-body text-[13px]">${html}</div>
    ${sources ? `<div class="mt-4 pt-3 border-t apple-border">
      <div class="text-[10.5px] uppercase tracking-wider text-apple-tertiary mb-1">${escapeHtml(t('libraryAskSources'))}</div>
      ${sources}
    </div>` : ''}
    ${data.model ? `<div class="mt-2 text-[10.5px] text-apple-tertiary">${data.cloud ? '☁ ' : ''}${escapeHtml(data.cloud ? data.model : formatModelName(data.model))}${data.mode === 'full' ? ' · ' + escapeHtml(t('askModeFull')) : ''}${data.mode === 'digest' ? ' · ' + escapeHtml(t('digestCount').replace('{n}', data.count)) : ''}${data.excluded_local ? ' · ' + escapeHtml(t('askExcludedLocal').replace('{n}', data.excluded_local)) : ''}</div>` : ''}`;
}

function openLibrarySource(itemId, n) {
  const item = askThread.find(x => x.id === itemId);
  const s = ((item && item.data && item.data.sources) || []).find(x => x.n === n);
  if (!s) return;
  switchAppView('library');
  selectMeetingAndSeek(s.meeting_id, s.start);
}

// A click in the library list: the recording opens in the library, whatever part of the app is shown
function openMeeting(id) {
  if (state.appView !== 'library') switchAppView('library');
  return selectMeeting(id, true);
}

async function selectMeetingAndSeek(meetingId, startSec) {
  await selectMeeting(meetingId, true);
  switchWorkspaceTab('transcript');
  setTimeout(() => seekPlayer(startSec), 220);
}

// --- Library: recordings in folders, sorted by dragging ---
const ICON_LIBRARY = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z"/></svg>';
const ICON_FILE = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"/></svg>';
const ICON_FOLDER = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"/></svg>';
const ICON_MIC = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z"/></svg>';
const ICON_IMPORT = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/></svg>';
const ICON_CHEVRON = '<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>';

function folderIcon(f) {
  return f.system_key === 'recordings' ? ICON_MIC : f.system_key === 'imports' ? ICON_IMPORT : ICON_FOLDER;
}

// System folders first (recordings, then imports), then the user's folders by name
function sortedFolders(parentId) {
  const order = { recordings: 0, imports: 1 };
  return (state.folders || []).filter(f => (f.parent_id ?? null) === parentId).sort((a, b) => {
    const sa = a.system_key ? order[a.system_key] ?? 2 : 3, sb = b.system_key ? order[b.system_key] ?? 2 : 3;
    return sa - sb || folderName(a).localeCompare(folderName(b), undefined, { numeric: true });
  });
}

function collapsedFolders() {
  try { return new Set(JSON.parse(localStorage.getItem('verbascribe_folders_collapsed') || '[]')); } catch (e) { return new Set(); }
}

function toggleFolderCollapsed(id) {
  const set = collapsedFolders();
  if (set.has(id)) set.delete(id); else set.add(id);
  try { localStorage.setItem('verbascribe_folders_collapsed', JSON.stringify([...set])); } catch (e) {}
  renderMeetingsSidebar();
}

async function loadFolders() {
  try {
    const res = await fetch('/api/folders');
    if (res.ok) state.folders = await res.json();
  } catch (e) {
    console.error(e);
  }
}

async function createFolderPrompt(parentId) {
  hideFolderMenu();
  const name = (prompt(t('folderNamePrompt'), '') || '').trim();
  if (!name) return;
  const res = await fetch('/api/folders', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parent_id: parentId }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  if (parentId != null) {
    const set = collapsedFolders();
    if (set.delete(parentId)) try { localStorage.setItem('verbascribe_folders_collapsed', JSON.stringify([...set])); } catch (e) {}
  }
  await loadFolders();
  renderMeetingsSidebar();
}

async function patchFolder(id, body) {
  const res = await fetch(`/api/folders/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return false; }
  await loadFolders();
  renderMeetingsSidebar();
  return true;
}

async function moveMeetingToFolder(meetingId, folderId) {
  const m = state.meetings.find(x => x.id === meetingId);
  if (!m || (m.folder_id ?? null) === folderId) return;
  const res = await fetch(`/api/meetings/${meetingId}/folder`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder_id: folderId }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  m.folder_id = folderId;
  if (state.activeMeeting && state.activeMeeting.id === meetingId) state.activeMeeting.folder_id = folderId;
  renderMeetingsSidebar();
}

// Folder menu (right click or •••)
function openFolderMenu(e, id) {
  e.preventDefault();
  e.stopPropagation();
  hideContextMenu();
  const f = folderById(id);
  if (!f) return;
  state.contextFolderId = id;
  const menu = document.getElementById('folder-context-menu');
  document.getElementById('fctx-title').textContent = folderName(f);
  document.getElementById('fctx-delete').classList.toggle('hidden', !!f.system_key);
  const inherited = !f.local_only && folderLocalOnly(f.parent_id);
  document.getElementById('fctx-local-check').textContent = f.local_only || inherited ? '✓' : '';
  document.getElementById('fctx-local').disabled = inherited;
  document.getElementById('fctx-local').style.opacity = inherited ? '.5' : '';
  menu.classList.remove('hidden');
  menu.style.left = `${Math.min(e.clientX, window.innerWidth - 230)}px`;
  menu.style.top = `${Math.min(e.clientY, window.innerHeight - 190)}px`;
}

function hideFolderMenu() {
  document.getElementById('folder-context-menu')?.classList.add('hidden');
}

async function fctxRename() {
  hideFolderMenu();
  const f = folderById(state.contextFolderId);
  if (!f) return;
  const name = (prompt(t('folderRenamePrompt'), folderName(f)) || '').trim();
  if (!name || name === folderName(f)) return;
  await patchFolder(f.id, { name });
}

async function fctxToggleLocalOnly() {
  hideFolderMenu();
  const f = folderById(state.contextFolderId);
  if (!f) return;
  if (await patchFolder(f.id, { local_only: !f.local_only })) {
    showToast(!f.local_only ? t('localOnlyFolderOn') : t('localOnlyOff'));
    renderAnalysisEstimate();
  }
}

function fctxNewSubfolder() {
  createFolderPrompt(state.contextFolderId);
}

function fctxDigest() {
  hideFolderMenu();
  openLibraryAsk('', { type: 'folder', id: state.contextFolderId });
  makeDigest(null);
}

function fctxAsk() {
  hideFolderMenu();
  openLibraryAsk('', { type: 'folder', id: state.contextFolderId });
}

async function fctxDelete() {
  hideFolderMenu();
  const f = folderById(state.contextFolderId);
  if (!f || f.system_key) return;
  if (!confirm(t('folderDeleteConfirm').replace('{name}', folderName(f)))) return;
  const res = await fetch(`/api/folders/${f.id}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  await Promise.all([loadFolders(), loadMeetingsList()]);
  renderMeetingsSidebar();
}

// Drag and drop inside the library: a recording or a folder onto a folder, or onto the list (top level)
let _libDrag = null;

function libraryDragStart(e, type, id) {
  _libDrag = { type, id };
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', `${type}:${id}`);
  e.stopPropagation();
  requestAnimationFrame(() => document.getElementById('meetings-list').classList.add('lib-dragging'));
}

function libraryDropAllowed(folderId) {
  if (!_libDrag) return false;
  if (_libDrag.type === 'f') {
    if (folderId === _libDrag.id) return false;
    if (folderId != null && folderSubtreeIds(_libDrag.id).has(folderId)) return false;  // into itself
  }
  return true;
}

function libraryDragOver(e, folderId) {
  if (!libraryDropAllowed(folderId)) return;
  e.preventDefault();
  e.stopPropagation();
  e.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('#meetings-list .drop-target').forEach(el => { if (el !== e.currentTarget) el.classList.remove('drop-target'); });
  e.currentTarget.classList.add('drop-target');
}

function libraryDragLeave(e) {
  if (e.currentTarget.contains(e.relatedTarget)) return;
  e.currentTarget.classList.remove('drop-target');
}

function clearLibraryDrag() {
  _libDrag = null;
  document.querySelectorAll('#meetings-list .drop-target').forEach(el => el.classList.remove('drop-target'));
  document.getElementById('meetings-list')?.classList.remove('drop-target', 'lib-dragging');
}

async function libraryDrop(e, folderId) {
  if (!libraryDropAllowed(folderId)) return;
  e.preventDefault();
  e.stopPropagation();
  const d = _libDrag;
  clearLibraryDrag();
  if (d.type === 'm') await moveMeetingToFolder(d.id, folderId);
  else await patchFolder(d.id, { parent_id: folderId });
}

document.addEventListener('dragend', clearLibraryDrag);

// What a recording has been through, as small icons in the library: transcript, analysis,
// translations (with their languages) and dubs; the step running now pulses, a failed one is red
const REC_BADGE_ICONS = {
  transcript: '<path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 10h16M4 14h10M4 18h7"/>',
  summary: '<path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/>',
  translation: '<path stroke-linecap="round" stroke-linejoin="round" d="M10.5 21l5.25-11.25L21 21m-9-3h7.5M3 5.621a48.474 48.474 0 016-.371m0 0c1.12 0 2.233.038 3.334.114M9 5.25V3m3.334 2.364C11.176 10.658 7.69 15.08 3 17.502m9.334-12.138c.896.061 1.785.147 2.666.257m-4.589 8.495a18.023 18.023 0 01-3.827-5.802"/>',
  dub: '<path stroke-linecap="round" stroke-linejoin="round" d="M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75z"/>',
};
const REC_STEP_OF_STATUS = {
  queued: 'transcript', extracting_audio: 'transcript', transcribing: 'transcript', diarizing: 'transcript',
  summarizing: 'summary', translating: 'translation', dubbing: 'dub', rendering: 'dub',
};

function recordingBadgesHtml(m) {
  const running = REC_STEP_OF_STATUS[m.status];
  const failedStep = m.status === 'failed' ? (m.has_transcript ? null : 'transcript') : null;
  const badge = (kind, done, label, text = '') => {
    const busy = running === kind;
    const fail = failedStep === kind;
    if (!done && !busy && !fail) return '';
    const tip = busy ? `${label} · ${t('recBadgeRunning')}` : fail ? `${t('statusFailed')}${m.error_message ? ': ' + m.error_message : ''}` : label;
    return `<span class="rec-badge ${busy ? 'rec-badge-busy' : ''} ${fail ? 'rec-badge-fail' : ''}" title="${escapeHtml(tip)}">`
      + `<svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">${REC_BADGE_ICONS[kind]}</svg>`
      + (text ? `<span>${escapeHtml(text)}</span>` : '') + '</span>';
  };
  const trLangs = m.translations_langs || [];
  const dubLangs = m.dub_langs || [];
  const lock = meetingLocalOnly(m) ? `<span class="rec-badge" title="${escapeHtml(t('tipLocalOnly'))}">${ICON_LOCK}</span>` : '';
  return lock + badge('transcript', m.has_transcript, t('recBadgeTranscript'))
    + badge('summary', m.has_summary, t('recBadgeSummary'))
    + badge('translation', trLangs.length > 0, `${t('recBadgeTranslation')}${trLangs.length ? ': ' + trLangs.map(langName).join(', ') : ''}`, trLangs.map(langCode).join(' '))
    + badge('dub', dubLangs.length > 0, `${t('recBadgeDub')}${dubLangs.length ? ': ' + dubLangs.map(langName).join(', ') : ''}`, dubLangs.map(langCode).join(' '));
}

function meetingRowHtml(m, depth) {
  const isSelected = state.activeMeeting && state.activeMeeting.id === m.id;
  const isBusy = !DONE_STATUSES.includes(m.status);
  const spkCount = Object.keys(m.speakers || {}).length;
  const thumb = m.is_video
    ? `<img src="/api/meetings/${m.id}/thumb" loading="lazy" alt="" draggable="false" onerror="this.remove()"
        class="w-12 h-[27px] rounded object-cover bg-black/60 shrink-0 border border-white/10" />`
    : '';
  const status = m.status === 'failed' ? t('statusFailed') : m.status === 'cancelled' ? t('statusCancelled')
    : `${spkCount || 1} ${t('spkShort')} · ${(m.language || 'ru').toUpperCase()}`;
  return `
    <div onclick="openMeeting(${m.id}, true)"
         oncontextmenu="openRecordingContextMenu(event, ${m.id})"
         draggable="true" ondragstart="libraryDragStart(event, 'm', ${m.id})"
         class="group cursor-pointer pr-2 py-1.5 rounded-lg transition relative flex items-center gap-2 ${
           isSelected ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'
         }" style="padding-left:${10 + depth * 14}px">
      ${thumb}
      <div class="min-w-0 flex-1">
        <div class="flex items-center justify-between gap-2">
          <span title="${escapeHtml(m.title)}" class="text-[12.5px] font-medium leading-snug line-clamp-2 break-words min-w-0">${escapeHtml(m.title)}</span>
          <button onclick="openRecordingContextMenu(event, ${m.id})" title="${escapeHtml(t('tipItemMenu'))}"
            class="opacity-0 group-hover:opacity-100 px-1 rounded hover:bg-black/20 text-xs shrink-0 transition">•••</button>
        </div>
        <div class="flex items-center justify-between gap-2 text-[11px] mt-0.5 ${isSelected ? 'text-white/80' : 'text-apple-secondary'}">
          <span class="flex items-center gap-1.5 min-w-0">
            <span class="font-mono shrink-0">${isBusy ? `${m.progress}%` : formatTime(m.duration)}</span>
            <span class="rec-badges">${recordingBadgesHtml(m)}</span>
          </span>
          <span class="truncate shrink-0 max-w-[55%]">${escapeHtml(libSortValue(m))} · ${escapeHtml(status)}</span>
        </div>
        ${isBusy ? `
          <div class="w-full h-1 bg-black/25 rounded-full overflow-hidden mt-1">
            <div class="h-full bg-white transition-all duration-300" style="width: ${m.progress}%"></div>
          </div>` : ''}
      </div>
    </div>`;
}

function shortDate(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return '';
  const thisYear = String(new Date().getFullYear()) === m[1];
  return thisYear ? `${m[3]}.${m[2]}` : `${m[3]}.${m[2]}.${m[1].slice(2)}`;
}

// --- Library view and sorting (kept per computer) ---
const LIB_VIEWS = ['details', 'compact', 'grid'];
const LIB_SORTS = ['recorded', 'added', 'name', 'duration', 'size'];

function libPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem('verbascribe_library_view') || '{}') || {}; } catch (e) {}
  return {
    view: LIB_VIEWS.includes(p.view) ? p.view : 'details',
    sort: LIB_SORTS.includes(p.sort) ? p.sort : 'recorded',
    dir: p.dir === 'asc' ? 'asc' : 'desc',
  };
}

function setLibPref(key, value) {
  const p = libPrefs();
  // Choosing the sort that is already on flips its direction
  if (key === 'sort' && p.sort === value) p.dir = p.dir === 'asc' ? 'desc' : 'asc';
  else if (key === 'sort') p.dir = value === 'name' ? 'asc' : 'desc';
  if (key !== 'dir') p[key] = value;
  try { localStorage.setItem('verbascribe_library_view', JSON.stringify(p)); } catch (e) {}
  renderMeetingsSidebar();
}

function libComparator({ sort, dir }) {
  const key = {
    recorded: m => m.recorded_at || m.created_at || '',
    added: m => m.created_at || '',
    name: m => (m.title || '').toLocaleLowerCase(),
    duration: m => Number(m.duration) || 0,
    size: m => Number(m.size_bytes) || 0,
  }[sort];
  const sign = dir === 'asc' ? 1 : -1;
  return (a, b) => {
    const x = key(a), y = key(b);
    const c = typeof x === 'string' ? x.localeCompare(y, undefined, { numeric: true }) : x - y;
    return (c || b.id - a.id) * sign;
  };
}

function openLibViewMenu(e) {
  e.preventDefault();
  e.stopPropagation();
  const had = document.getElementById('lib-view-menu');
  closeLibViewMenu();
  if (had) return;
  const p = libPrefs();
  const item = (key, value, label, on, extra = '') => `<button class="macos-menu-item" data-k="${key}" data-v="${value}">`
    + `<span>${escapeHtml(label)}</span><span class="text-[10.5px] opacity-70">${on ? (extra || '✓') : ''}</span></button>`;
  const arrow = p.dir === 'asc' ? '↑' : '↓';
  const menu = document.createElement('div');
  menu.id = 'lib-view-menu';
  menu.className = 'fixed w-56 macos-menu z-50';
  menu.innerHTML = `<div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('libView'))}</div>`
    + LIB_VIEWS.map(v => item('view', v, t(`libView_${v}`), p.view === v)).join('')
    + `<div class="my-1 h-px bg-white/10"></div><div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('libSort'))}</div>`
    + LIB_SORTS.map(v => item('sort', v, t(`libSort_${v}`), p.sort === v, arrow)).join('');
  menu.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-k]');
    ev.stopPropagation();
    if (!b) return;
    setLibPref(b.dataset.k, b.dataset.v);
    closeLibViewMenu();
  });
  document.body.appendChild(menu);
  const r = e.currentTarget.getBoundingClientRect();
  menu.style.left = `${Math.min(r.left, window.innerWidth - 232)}px`;
  menu.style.top = `${r.bottom + 4}px`;
}

function closeLibViewMenu() {
  document.getElementById('lib-view-menu')?.remove();
}

document.addEventListener('click', closeLibViewMenu);

function formatSize(bytes) {
  if (!bytes) return '';
  const mb = bytes / 1048576;
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} ${t('unitGb')}` : `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} ${t('unitMb')}`;
}

// What the row shows on its right: the sorted-by value when it is not the date
function libSortValue(m) {
  const { sort } = libPrefs();
  if (sort === 'size') return formatSize(m.size_bytes);
  if (sort === 'added') return shortDate(m.created_at);
  return shortDate(m.recorded_at || m.created_at);
}

function meetingCompactRowHtml(m, depth) {
  const isSelected = state.activeMeeting && state.activeMeeting.id === m.id;
  const isBusy = !DONE_STATUSES.includes(m.status);
  return `
    <div onclick="openMeeting(${m.id}, true)" oncontextmenu="openRecordingContextMenu(event, ${m.id})"
         draggable="true" ondragstart="libraryDragStart(event, 'm', ${m.id})" title="${escapeHtml(m.title)}"
         class="group cursor-pointer pr-2 py-1 rounded-md transition flex items-center gap-2 text-[12px] ${
           isSelected ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'
         }" style="padding-left:${10 + depth * 14}px">
      <span class="shrink-0 ${isSelected ? 'text-white/80' : 'text-apple-tertiary'}">${m.is_video ? ICON_FILE_VIDEO : ICON_FILE_AUDIO}</span>
      <span class="truncate flex-1 min-w-0">${escapeHtml(m.title)}</span>
      <span class="rec-badges shrink-0 ${isSelected ? 'text-white/80' : 'text-apple-secondary'}">${recordingBadgesHtml(m)}</span>
      <span class="font-mono text-[10.5px] shrink-0 ${isSelected ? 'text-white/80' : 'text-apple-tertiary'}">${isBusy ? `${m.progress}%` : libPrefs().sort === 'size' ? formatSize(m.size_bytes) : formatTime(m.duration)}</span>
    </div>`;
}

function meetingTileHtml(m) {
  const isSelected = state.activeMeeting && state.activeMeeting.id === m.id;
  const isBusy = !DONE_STATUSES.includes(m.status);
  const cover = m.is_video
    ? `<img src="/api/meetings/${m.id}/thumb" loading="lazy" alt="" draggable="false" onerror="this.replaceWith(Object.assign(document.createElement('span'), {className: 'lib-tile-icon', innerHTML: ICON_FILE_VIDEO}))" class="w-full h-full object-cover" />`
    : `<span class="lib-tile-icon">${ICON_FILE_AUDIO}</span>`;
  return `
    <div onclick="openMeeting(${m.id}, true)" oncontextmenu="openRecordingContextMenu(event, ${m.id})"
         draggable="true" ondragstart="libraryDragStart(event, 'm', ${m.id})" title="${escapeHtml(m.title)}"
         class="lib-tile group ${isSelected ? 'lib-tile-on' : ''}">
      <div class="lib-tile-cover">
        ${cover}
        <span class="lib-tile-time">${isBusy ? `${m.progress}%` : formatTime(m.duration)}</span>
        ${isBusy ? `<span class="lib-tile-progress" style="width:${m.progress}%"></span>` : ''}
      </div>
      <div class="px-1.5 pt-1 pb-1.5 min-w-0">
        <div class="text-[11.5px] font-medium leading-tight line-clamp-2">${escapeHtml(m.title)}</div>
        <div class="flex items-center justify-between gap-1 mt-1 text-[10px] ${isSelected ? 'text-white/80' : 'text-apple-secondary'}">
          <span class="rec-badges">${recordingBadgesHtml(m)}</span>
          <span class="shrink-0">${escapeHtml(libSortValue(m))}</span>
        </div>
      </div>
    </div>`;
}

const ICON_FILE_AUDIO = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.7" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 9l10.5-3m0 6.553v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 11-.99-3.467l2.31-.66a2.25 2.25 0 001.632-2.163zm0 0V2.25L9 5.25v10.303m0 0v3.75a2.25 2.25 0 01-1.632 2.163l-1.32.377a1.803 1.803 0 01-.99-3.467l2.31-.66A2.25 2.25 0 009 15.553z"/></svg>';
const ICON_FILE_VIDEO = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.7" viewBox="0 0 24 24"><path stroke-linecap="round" d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z"/></svg>';

function folderRowHtml(f, depth, collapsed) {
  const count = meetingsInFolderTree(f.id).length;
  const open = !collapsed.has(f.id);
  return `
    <div onclick="toggleFolderCollapsed(${f.id})" oncontextmenu="openFolderMenu(event, ${f.id})"
         ${f.system_key ? '' : `draggable="true" ondragstart="libraryDragStart(event, 'f', ${f.id})"`}
         ondragover="libraryDragOver(event, ${f.id})" ondragleave="libraryDragLeave(event)" ondrop="libraryDrop(event, ${f.id})"
         class="folder-row group flex items-center gap-1.5 pr-2 py-1.5 rounded-lg cursor-pointer text-apple-label hover:bg-white/5 transition"
         style="padding-left:${6 + depth * 14}px" title="${escapeHtml(folderName(f))}">
      <span class="shrink-0 text-apple-tertiary transition-transform ${open ? 'rotate-90' : ''}">${ICON_CHEVRON}</span>
      <span class="shrink-0 ${f.system_key ? 'text-apple-blue' : 'text-apple-secondary'}">${folderIcon(f)}</span>
      <span class="text-[12px] font-semibold truncate flex-1">${escapeHtml(folderName(f))}</span>
      <span class="text-[10.5px] font-mono text-apple-tertiary">${count}</span>
      <button onclick="openFolderMenu(event, ${f.id})" title="${escapeHtml(t('tipFolderMenu'))}"
        class="opacity-0 group-hover:opacity-100 px-1 rounded hover:bg-black/20 text-xs shrink-0 transition">•••</button>
    </div>`;
}

function renderMeetingsSidebar() {
  const listEl = document.getElementById('meetings-list');

  if (state.globalSearchQuery && state.globalSearchResults) {
    const askHtml = `
      <button onclick="openLibraryAsk(state.globalSearchQuery, {type: 'all'})" title="${escapeHtml(t('tipLibraryAsk'))}"
        class="w-full mb-1.5 px-2.5 py-2 rounded-lg border border-apple-blue/30 bg-apple-blue/10 hover:bg-apple-blue/20 text-left text-[11.5px] text-white transition flex items-center gap-2">
        ${SVG_SPARK}<span class="truncate">${escapeHtml(t('libraryAskFromSearch'))}</span>
      </button>`;
    if (state.globalSearchResults.length === 0) {
      listEl._keyed = false;
      listEl.innerHTML = askHtml + `<div class="px-3 py-8 text-center text-xs text-apple-tertiary">${escapeHtml(t('noMatches'))}</div>`;
      return;
    }
    listEl._keyed = false;
    listEl.innerHTML = askHtml + state.globalSearchResults.map(item => `
      <div class="rounded-lg bg-white/[0.03] border apple-border p-2.5 space-y-1.5 mb-1.5">
        <div onclick="openMeeting(${item.meeting_id}, true)"
             title="${escapeHtml(item.title)}"
             class="text-xs font-semibold text-white hover:text-apple-blue cursor-pointer truncate">
          ${escapeHtml(item.title)}
        </div>
        ${(item.hits || []).map(h => `
          <div onclick="switchAppView('library'); selectMeetingAndSeek(${item.meeting_id}, ${h.start})"
               title="${escapeHtml(t('tipSeekTimestamp'))}"
               class="px-2 py-1 rounded bg-black/30 hover:bg-apple-blue/20 cursor-pointer text-[11px] transition">
            <div class="flex items-center justify-between text-[10px] text-apple-blue font-mono">
              <span>${escapeHtml(h.speaker)}${h.semantic ? ` <span class="text-apple-tertiary" title="${escapeHtml(t('tipSemanticHit'))}">· ${escapeHtml(t('semanticHit'))}</span>` : ''}</span>
              <span>${formatTime(h.start)}</span>
            </div>
            <div class="text-apple-label/90 line-clamp-2 mt-0.5">${highlightSearch(escapeHtml(h.text), state.globalSearchQuery)}</div>
          </div>
        `).join('')}
      </div>
    `).join('');
    return;
  }

  const folders = state.folders || [];
  if (state.meetings.length === 0 && !folders.some(f => !f.system_key)) {
    listEl._keyed = false;
    listEl.innerHTML = `<div class="px-3 py-8 text-center text-xs text-apple-tertiary">${t('noRecordings')}</div>`;
    return;
  }

  const known = new Set(folders.map(f => f.id));
  const byFolder = new Map();
  for (const m of state.meetings) {
    const key = known.has(m.folder_id) ? m.folder_id : null;
    if (!byFolder.has(key)) byFolder.set(key, []);
    byFolder.get(key).push(m);
  }
  const prefs = libPrefs();
  for (const list of byFolder.values()) list.sort(libComparator(prefs));
  // Every row is a keyed part: a progress event then replaces only the row that changed
  const parts = [];
  const items = (list, depth, groupKey) => {
    if (!list || !list.length) return;
    if (prefs.view === 'grid') {
      parts.push([`g${groupKey}`, `<div class="lib-grid" style="padding-left:${6 + depth * 14}px">${list.map(meetingTileHtml).join('')}</div>`]);
      return;
    }
    for (const m of list) parts.push([`m${m.id}`, prefs.view === 'compact' ? meetingCompactRowHtml(m, depth) : meetingRowHtml(m, depth)]);
  };
  const collapsed = collapsedFolders();
  const walk = (parentId, depth) => {
    for (const f of sortedFolders(parentId)) {
      // An empty system folder only takes room
      if (f.system_key && !meetingsInFolderTree(f.id).length && !sortedFolders(f.id).length) continue;
      parts.push([`f${f.id}`, folderRowHtml(f, depth, collapsed)]);
      if (collapsed.has(f.id)) continue;
      walk(f.id, depth + 1);
      items(byFolder.get(f.id), depth + 1, f.id);
    }
  };
  walk(null, 0);
  items(byFolder.get(null), 0, 'root');
  patchKeyedList(listEl, parts);
}

// Updates a list from [key, html] parts, touching only the elements whose html changed and
// moving the rest into place: thumbnails are not reloaded and hover states survive an update
function patchKeyedList(box, parts) {
  if (!box._keyed) {
    box.innerHTML = '';
    box._keyed = true;
    box._html = new Map();
  }
  const old = new Map([...box.children].map(el => [el.dataset.key, el]));
  const make = (key, html) => {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    const el = tpl.content.firstElementChild;
    el.dataset.key = key;
    return el;
  };
  let prev = null;
  for (const [key, html] of parts) {
    let el = old.get(key);
    if (!el) {
      el = make(key, html);
    } else if (box._html.get(key) !== html) {
      const fresh = make(key, html);
      el.replaceWith(fresh);
      el = fresh;
    }
    old.delete(key);
    box._html.set(key, html);
    const want = prev ? prev.nextElementSibling : box.firstElementChild;
    if (el !== want) box.insertBefore(el, want);
    prev = el;
  }
  for (const [key, el] of old) {
    el.remove();
    box._html.delete(key);
  }
}

// --- Files dragged onto the window: a full-window overlay shows that they can be dropped ---
const MEDIA_EXT = /\.(mp4|mkv|mov|webm|avi|m4v|mpg|mpeg|ts|mts|3gp|wmv|flv|mp3|wav|m4a|aac|flac|ogg|oga|opus|wma|amr|aiff?)$/i;
let _dragDepth = 0;
let _dragSupported = true;

function isFileDrag(e) {
  return !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
}

function isMediaFile(file) {
  return /^(audio|video)\//.test(file.type || '') || MEDIA_EXT.test(file.name || '');
}

// While dragging only MIME types are known (no names); an empty type is given the benefit of the doubt
function dragLooksSupported(e) {
  const items = Array.from(e.dataTransfer.items || []).filter(i => i.kind === 'file');
  return !items.length || items.some(i => !i.type || /^(audio|video)\//.test(i.type));
}

function showDropOverlay(supported) {
  const el = document.getElementById('drop-overlay');
  el.classList.toggle('unsupported', !supported);
  document.getElementById('drop-overlay-title').textContent = t(supported ? 'dropOverlayTitle' : 'dropOverlayUnsupported');
  document.getElementById('drop-overlay-sub').textContent = t(supported ? 'dropOverlaySub' : 'dropOverlayFormats');
  el.classList.remove('hidden');
}

function hideDropOverlay() {
  _dragDepth = 0;
  document.getElementById('drop-overlay')?.classList.add('hidden');
}

document.addEventListener('dragenter', (e) => {
  if (!isFileDrag(e)) return;
  _dragDepth++;
  if (_dragDepth === 1) {
    _dragSupported = dragLooksSupported(e);
    showDropOverlay(_dragSupported);
  }
});
document.addEventListener('dragleave', (e) => {
  if (!isFileDrag(e)) return;
  _dragDepth = Math.max(0, _dragDepth - 1);
  if (_dragDepth === 0) hideDropOverlay();
});
document.addEventListener('drop', hideDropOverlay, true);
document.addEventListener('dragend', hideDropOverlay);

function onWindowDragOver(e) {
  e.preventDefault();
  if (isFileDrag(e)) e.dataTransfer.dropEffect = _dragSupported ? 'copy' : 'none';
}

function dropMediaFiles(e) {
  e.preventDefault();
  hideDropOverlay();
  const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
  if (!files.length) return;
  const media = files.filter(isMediaFile);
  if (media.length < files.length) {
    const skipped = files.filter(f => !isMediaFile(f)).map(f => f.name).slice(0, 3).join(', ');
    showToast(t('dropSkipped').replace('{names}', skipped));
  }
  if (media.length) handleFileSelect(media);
}

function handleGlobalDrop(e) {
  dropMediaFiles(e);
}

function handleFileDrop(e) {
  dropMediaFiles(e);
}

// Sends files to the server; `source` tells a recording made here from an imported file (they go to
// different folders on disk and in the library). The file dates are sent too: for an imported file
// the date it was last modified is the closest to when the meeting took place.
async function uploadFiles(fileList, opts = {}) {
  const fd = new FormData();
  for (const f of fileList) {
    fd.append('files', f);
    fd.append('last_modified', String(f.lastModified || 0));
  }
  fd.append('model_name', document.getElementById('upload-model').value);
  fd.append('language', document.getElementById('upload-language').value);
  fd.append('num_speakers', opts.source === 'recording' ? '0' : document.getElementById('upload-speakers').value);
  fd.append('source', opts.source || 'import');
  if (opts.title) fd.append('title', opts.title);
  if (opts.folderId != null) fd.append('folder_id', String(opts.folderId));
  const res = await fetch('/api/meetings/upload', { method: 'POST', body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data.created || [];
}

async function handleFileSelect(fileList) {
  if (!fileList || fileList.length === 0) return;
  const statusEl = document.getElementById('upload-status-text');
  statusEl.classList.remove('hidden');
  statusEl.textContent = t('uploadingFiles').replace('{n}', fileList.length);
  switchAppView('library');

  try {
    const created = await uploadFiles(Array.from(fileList), { source: 'import' });
    statusEl.classList.add('hidden');
    document.getElementById('file-input').value = '';
    closeModal('import');
    await loadMeetingsList();
    if (created.length > 0) {
      await selectMeeting(created[0].id, true);
      return created;
    }
  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
    showToast(String(err.message || err));
  }
}

async function submitLocalPath() {
  const pathInput = document.getElementById('local-path-input');
  const path = pathInput.value.trim();
  if (!path) return;

  const res = await fetch('/api/meetings/local-path', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      path,
      model_name: document.getElementById('upload-model').value,
      language: document.getElementById('upload-language').value,
      num_speakers: Number(document.getElementById('upload-speakers').value || 0),
    })
  });

  const data = await res.json();
  if (!res.ok) {
    alert(data.detail || 'Error');
    return;
  }

  pathInput.value = '';
  closeModal('import');
  await loadMeetingsList();
  if (data.created && data.created.length > 0) {
    await selectMeeting(data.created[0].id, true);
  }
}

// --- Sidebar width: dragged on its right edge, kept per computer ---
const SIDEBAR_MIN = 240, SIDEBAR_MAX = 560, SIDEBAR_DEFAULT = 312;

function setSidebarWidth(w, save) {
  const aside = document.getElementById('app-sidebar');
  if (!aside) return;
  w = Math.round(Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, Math.min(w, window.innerWidth - 560))));
  aside.style.width = `${w}px`;
  if (save) try { localStorage.setItem('verbascribe_sidebar_width', String(w)); } catch (e) {}
}

(function initSidebarResize() {
  try { const w = Number(localStorage.getItem('verbascribe_sidebar_width')); if (w) setSidebarWidth(w, false); } catch (e) {}
  const handle = document.getElementById('sidebar-resizer');
  if (!handle) return;
  handle.addEventListener('dblclick', () => setSidebarWidth(SIDEBAR_DEFAULT, true));
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add('dragging');
    document.body.classList.add('sidebar-dragging');
    const left = document.getElementById('app-sidebar').getBoundingClientRect().left;
    const move = (ev) => setSidebarWidth(ev.clientX - left, false);
    const up = (ev) => {
      setSidebarWidth(ev.clientX - left, true);
      handle.classList.remove('dragging');
      document.body.classList.remove('sidebar-dragging');
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
})();
