// VerbaScribe page script, part 4 of 9: Library: the list, live updates, the queue, app views, the assistant, folders, views and sorting, dropped files.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// --- Meetings List, Global Search & Sidebar ---
async function loadMeetingsList() {
  const [res] = await Promise.all([fetch('/api/meetings'), loadFolders()]);
  const list = await readJson(res);
  if (!res.ok || !Array.isArray(list)) return;  // the list shown stays (a lost session reloads the page)
  state.meetings = list;
  onLibraryChanged();
  // The empty editor points to the list on the left when there is something in it
  document.getElementById('workspace-empty-pick')?.classList.toggle('hidden', !list.length);
  if (!state.globalSearchQuery) {
    renderMeetingsSidebar();
  } else if (typeof rerunGlobalSearch === 'function') {
    rerunGlobalSearch();  // deleted or renamed recordings must not stay in the results
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
    // The top row as the list is sorted (not the newest id), scrolled into view
    const first = libVisibleIds()[0] ?? state.meetings[0].id;
    await selectMeeting(first, true);
    document.querySelector(`#meetings-list [data-mid="${first}"]`)?.scrollIntoView({ block: 'nearest' });
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
  es.onopen = () => {
    live.connected = true;
    // An event sent while the stream was reconnecting is lost: a call that ended meanwhile must
    // still stop its recording
    if (state.callRecording) {
      fetch('/api/calls/status').then(r => r.json()).then(st => {
        if (state.callRecording && st && st.active !== state.callRecording) {
          handleCallEvent({ state: 'ended', app: state.callRecording });
        }
      }).catch(() => {});
    }
  };
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
    text += ' · ' + (mins < 1 ? t('etaUnderMinute') : t('etaMinutes').replace('{n}', () => mins));
  }
  return text;
}

// Re-render the elapsed time every second while something is running
setInterval(() => {
  if (document.hidden || !(state.queue || []).some(q => q.state === 'running')) return;
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
  state.queue = await readJson(res);
  renderQueuePanel();
  renderStatusbar();
}

function queueKindLabel(q) {
  const lang = (q.lang || '').toUpperCase();
  if (q.kind === 'dub') return `${t('queueKindDub')} → ${lang}`;
  if (q.kind === 'translate') return `${t('queueKindTranslate')} → ${lang}`;
  if (q.kind === 'burn_subs') return `${t('queueKindBurnSubs')} (${lang})`;
  if (q.kind === 'cut') return t('queueKindCut');
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
      : running ? serverText(m.stage_text || q.stage_text || '') : t('queueWaiting').replace('{n}', () => q.position);
    const timing = running && !q.cancelling ? formatTiming(jobTiming(q, pct)) : '';
    return `
      <div data-click="openMeeting" data-click-args='[${q.id}, true]' tabindex="0" role="button" title="${escapeHtml(detail)}"
        class="group px-2 py-1.5 rounded-md cursor-pointer hover:bg-white/5 ${running ? 'bg-apple-blue/10' : ''}">
        <div class="flex items-center justify-between gap-2">
          <span class="flex items-center gap-1.5 min-w-0">
            ${running && !q.cancelling ? '<span class="live-dot"></span>' : ''}
            <span class="text-[12px] font-medium text-white truncate">${escapeHtml(q.title)}</span>
          </span>
          <button data-click="stopThen" data-click-args='[{"$": "event"}, "cancelProcessing", ${Number(q.id) || 0}]' title="${escapeHtml(t('tipQueueCancel'))}"
            class="shrink-0 text-apple-tertiary hover:text-apple-red opacity-80 group-hover:opacity-100 transition ${q.cancelling ? 'hidden' : ''}">${SVG_ICONS.closeX}</button>
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
  // Text recognized so far, while the recording is still being transcribed
  if (ev.type === 'segments') {
    if (state.activeMeeting && state.activeMeeting.id === ev.id) selectMeeting(ev.id, false);
    return;
  }
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
  // Live updates come by events; the poll is the fallback. A hidden window (tray) asks rarely and
  // catches up when it is shown again (app.js visibilitychange)
  setTimeout(pollMeetingsLoop, live.connected || document.hidden ? 30000 : (busy ? 2000 : 8000));
}

async function handleGlobalSidebarSearch(rawVal) {
  const q = (rawVal || '').trim();
  state.globalSearchQuery = q;
  clearTimeout(window._globalSearchDebounce);  // also when cleared: a pending search must not come back
  if (!q) {
    state.globalSearchResults = null;
    renderMeetingsSidebar();
    return;
  }
  window._globalSearchDebounce = setTimeout(() => runGlobalSearch(q), 180);
}

async function runGlobalSearch(q) {
  const res = await fetch(`/api/meetings/search?q=${encodeURIComponent(q)}`);
  const data = await readJson(res);
  if (q !== state.globalSearchQuery) return;  // an older search answered late
  state.globalSearchResults = Array.isArray(data) ? data : [];
  renderMeetingsSidebar();
}

function rerunGlobalSearch() {
  if (state.globalSearchQuery) runGlobalSearch(state.globalSearchQuery);
}

// The views of the main area (library, studio, questions…) and switchAppView: static/js/shell.js

// --- Questions: about one recording, a folder (with its subfolders) or the whole library ---
const SVG_SPARK = '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>';
state.askScope = { type: 'all' };
const askThread = [];
let _askSeq = 0;
// The id of a question, a digest or a brief in the assistant: unique across the saved conversations
// (they outlive the page), so a source link or «Спросить снова» finds its own item
function newAskItemId() { return Date.now() * 1000 + (++_askSeq % 1000); }

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
  if (scope.type === 'series') return (state.series || []).some(s => s.id === scope.id);
  if (scope.type === 'meeting') return state.meetings.some(m => m.id === scope.id);
  return true;
}

function askScopeLabel(scope) {
  if (scope.type === 'folder') {
    const f = folderById(scope.id);
    return `${t('askScopeFolder')}: ${folderName(f)} · ${t('askRecordingsCount').replace('{n}', () => meetingsInFolderTree(scope.id).length)}`;
  }
  if (scope.type === 'meeting') {
    const m = state.meetings.find(x => x.id === scope.id);
    return `${t('askScopeMeeting')}: ${m ? m.title : ''}`;
  }
  if (scope.type === 'series') {
    const s = (state.series || []).find(x => x.id === scope.id);
    return `${t('askScopeSeries')}: ${s ? s.name : ''} · ${t('askRecordingsCount').replace('{n}', () => s ? s.count : 0)}`;
  }
  if (scope.type === 'person') {
    const p = personByName(scope.name);
    return `${t('askScopePerson').replace('{name}', () => scope.name)} · ${t('askRecordingsCount').replace('{n}', () => p ? p.recordings.length : 0)}`;
  }
  return `${t('askScopeAll')} · ${t('askRecordingsCount').replace('{n}', () => state.meetings.length)}`;
}

// --- People: everyone named in the library, and the recordings they speak in ---
state.people = [];

function personByName(name) {
  return (state.people || []).find(p => p.name.toLowerCase() === String(name || '').toLowerCase()) || null;
}

async function loadPeople() {
  [state.people] = await Promise.all([fetch('/api/people').then(r => r.ok ? r.json() : []).catch(() => []), loadSeries()]);
  if (state.appView === 'ask') renderAskScope();
}

function askScopePayload(scope) {
  if (scope.type === 'folder') return { folder_id: scope.id };
  if (scope.type === 'person') return { meeting_ids: (personByName(scope.name) || { recordings: [] }).recordings.map(r => r.meeting_id) };
  if (scope.type === 'series') return { meeting_ids: state.meetings.filter(m => m.series_id === scope.id).map(m => m.id) };
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
  // Before the next meeting of a series, or with a person
  const brief = scope.type === 'series' || scope.type === 'person'
    ? `<button class="macos-menu-item" data-brief="1"><span>${escapeHtml(t('briefBtn'))}</span></button><div class="my-1 h-px bg-white/10"></div>` : '';
  menu.innerHTML = brief + `<div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(where)}</div>`
    + [[7, t('digestWeek')], [30, t('digestMonth')], [0, t('digestEverything')]]
      .map(([d, label]) => `<button class="macos-menu-item" data-days="${d}"><span>${escapeHtml(label)}</span></button>`).join('');
  menu.addEventListener('click', (ev) => {
    if (ev.target.closest('button[data-brief]')) { ev.stopPropagation(); menu.remove(); makeBrief(); return; }
    const b = ev.target.closest('button[data-days]');
    ev.stopPropagation();
    if (!b) return;
    menu.remove();
    makeDigest(Number(b.dataset.days) || null);
  });
  document.body.appendChild(menu);
  const r = e.currentTarget.getBoundingClientRect();
  menu.style.left = `${Math.min(zpx(r.left), viewW() - 232)}px`;
  menu.style.top = `${zpx(r.bottom) + 4}px`;
}

document.addEventListener('click', () => document.getElementById('digest-menu')?.remove());

async function makeBrief() {
  const scope = { ...state.askScope };
  const item = { id: newAskItemId(), question: t('briefBtn'), scopeLabel: askScopeLabel(scope), status: 'pending', data: null, error: '' };
  askThread.unshift(item);
  renderAskThread();
  try {
    const res = await fetch('/api/library/brief', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ series_id: scope.type === 'series' ? scope.id : null, person: scope.type === 'person' ? scope.name : null,
        lang: state.uiLang === 'en' ? 'en' : 'ru' }),
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

async function makeDigest(days) {
  const scope = state.askScope.type === 'folder' ? { ...state.askScope } : { type: 'all' };
  const period = days === 7 ? t('digestWeek') : days === 30 ? t('digestMonth') : t('digestEverything');
  const item = { id: newAskItemId(), question: `${t('digestBtn')} · ${period}`, scopeLabel: askScopeLabel(scope), status: 'pending', data: null, error: '' };
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

// "[2]" in an answer becomes a button to its source. Text nodes are rewritten, never the sanitized
// HTML string: a [2] inside a link title or an image's alt text stays text there
function linkifyCitations(root, byN, itemId) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (/\[\d{1,3}\]/.test(n.nodeValue) && !n.parentElement.closest('button, a, code')) nodes.push(n);
  }
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of node.nodeValue.matchAll(/\[(\d{1,3})\]/g)) {
      const src = byN[m[1]];
      if (!src) continue;
      const b = document.createElement('button');
      b.className = 'lib-cite';
      b.textContent = m[1];
      b.title = `${src.title} · ${formatTime(src.start)}`;
      b.addEventListener('click', () => openLibrarySource(itemId, Number(m[1])));
      frag.append(node.nodeValue.slice(last, m.index), b);
      last = m.index + m[0].length;
    }
    if (!last) continue;
    frag.append(node.nodeValue.slice(last));
    node.replaceWith(frag);
  }
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
const ICON_DICTATION = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z"/></svg>';
const ICON_FOLD_ALL = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5L12 8.25 15.75 4.5m-7.5 15L12 15.75l3.75 3.75"/></svg>';
const ICON_UNFOLD_ALL = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 15L12 18.75 15.75 15m-7.5-6L12 5.25 15.75 9"/></svg>';
const ICON_CHEVRON = '<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5"/></svg>';

function folderIcon(f) {
  return f.system_key === 'recordings' ? ICON_MIC : f.system_key === 'imports' ? ICON_IMPORT
    : f.system_key === 'dictations' ? ICON_DICTATION : ICON_FOLDER;
}

// System folders first (recordings, then imports), then the user's folders by name
function sortedFolders(parentId) {
  const order = { recordings: 0, imports: 1, dictations: 2 };
  return (state.folders || []).filter(f => (f.parent_id ?? null) === parentId).sort((a, b) => {
    const sa = a.system_key ? order[a.system_key] ?? 3 : 4, sb = b.system_key ? order[b.system_key] ?? 3 : 4;
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
    if (res.ok) state.folders = await readJson(res);
  } catch (e) {
    console.error(e);
  }
}

async function createFolderPrompt(parentId) {
  hideFolderMenu();
  const name = (await askText(t('folderNamePrompt'), '') || '').trim();
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
  menu.style.left = `${Math.min(zpx(e.clientX), viewW() - 230)}px`;
  menu.style.top = `${Math.min(zpx(e.clientY), viewH() - 190)}px`;
}

function hideFolderMenu() {
  document.getElementById('folder-context-menu')?.classList.add('hidden');
}

async function fctxRename() {
  hideFolderMenu();
  const f = folderById(state.contextFolderId);
  if (!f) return;
  const name = (await askText(t('folderRenamePrompt'), folderName(f)) || '').trim();
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
  if (!await askConfirm(t('folderDeleteConfirm').replace('{name}', () => folderName(f)), true)) return;
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
  summarizing: 'summary', translating: 'translation', dubbing: 'dub', rendering: 'dub', merging: 'transcript',
};

function recordingBadgesHtml(m) {
  const running = REC_STEP_OF_STATUS[m.status];
  const failedStep = m.status === 'failed' ? (m.has_transcript ? null : 'transcript') : null;
  // Always shown: what needs attention (a step running, a failure, a recording marked local by
  // itself). What a recording has (analysis, translations, dubs) shows on hover and on the selected
  // row, or always with "Badges: all" in the view menu; having a transcript is the norm: no badge
  const badge = (kind, done, label, text = '') => {
    const busy = running === kind;
    const fail = failedStep === kind;
    if (kind === 'transcript' && !busy && !fail) return '';
    if (!done && !busy && !fail) return '';
    const extra = !busy && !fail ? 'rec-badge-extra' : '';
    const tip = busy ? `${label} · ${t('recBadgeRunning')}` : fail ? `${t('statusFailed')}${m.error_message ? ': ' + humanError(m.error_message).short : ''}` : label;
    return `<span class="rec-badge ${extra} ${busy ? 'rec-badge-busy' : ''} ${fail ? 'rec-badge-fail' : ''}" title="${escapeHtml(tip)}">`
      + `<svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">${REC_BADGE_ICONS[kind]}</svg>`
      + (text ? `<span>${escapeHtml(text)}</span>` : '') + '</span>';
  };
  const trLangs = m.translations_langs || [];
  const dubLangs = m.dub_langs || [];
  // The lock of a whole folder is on the folder's row
  const lock = meetingLocalOnly(m) ? `<span class="rec-badge ${m.local_only ? '' : 'rec-badge-extra'}" title="${escapeHtml(t('tipLocalOnly'))}">${ICON_LOCK}</span>` : '';
  return lock + badge('transcript', m.has_transcript, t('recBadgeTranscript'))
    + badge('summary', m.has_summary, t('recBadgeSummary'))
    + badge('translation', trLangs.length > 0, `${t('recBadgeTranslation')}${trLangs.length ? ': ' + trLangs.map(langName).join(', ') : ''}`, trLangs.map(langCode).join(' '))
    + badge('dub', dubLangs.length > 0, `${t('recBadgeDub')}${dubLangs.length ? ': ' + dubLangs.map(langName).join(', ') : ''}`, dubLangs.map(langCode).join(' '));
}

function meetingRowHtml(m, depth) {
  const isSelected = libIsSelected(m);
  const isBusy = !DONE_STATUSES.includes(m.status);
  const spkCount = Object.keys(m.speakers || {}).length;
  const thumb = m.is_video
    ? `<img src="/api/meetings/${m.id}/thumb" loading="lazy" alt="" draggable="false" data-error="removeElement" data-error-args='[{"$": "el"}]'
        class="w-12 h-[27px] rounded object-cover bg-black/60 shrink-0 border border-white/10" />`
    : '';
  const status = m.status === 'failed' ? t('statusFailed') : m.status === 'cancelled' ? t('statusCancelled') : m.status === 'cleared' ? t('statusClearedShort')
    : `${spkCount || 1} ${t('spkShort')} · ${(m.language || 'ru').toUpperCase()}`;
  return `
    <div data-click="libRowClick" data-click-args='[{"$": "event"}, ${m.id}]' data-mid="${m.id}" tabindex="0" role="button"
         data-contextmenu="openRecordingContextMenu" data-contextmenu-args='[{"$": "event"}, ${m.id}]'
         draggable="true" data-dragstart="libraryDragStart" data-dragstart-args='[{"$": "event"}, "m", ${m.id}]'
         class="group cursor-pointer pr-2 py-1.5 rounded-lg transition relative flex items-center gap-2 ${
           isSelected ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'
         }" style="padding-left:${10 + depth * 14}px">
      ${thumb}
      <div class="min-w-0 flex-1">
        <div class="flex items-center justify-between gap-2">
          <span title="${escapeHtml(libTitle(m))}" class="text-[12.5px] font-medium leading-snug line-clamp-2 break-words min-w-0">${escapeHtml(libTitle(m))}</span>
          <button data-click="openRecordingContextMenu" data-click-args='[{"$": "event"}, ${m.id}]' title="${escapeHtml(t('tipItemMenu'))}"
            class="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 px-1 rounded hover:bg-black/20 text-xs shrink-0 transition">•••</button>
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

// Read once and kept: rendering asks for it for every row
let _libPrefsCache = null;

function libPrefs() {
  if (_libPrefsCache) return _libPrefsCache;
  let p = {};
  try { p = JSON.parse(localStorage.getItem('verbascribe_library_view') || '{}') || {}; } catch (e) {}
  return _libPrefsCache = {
    view: LIB_VIEWS.includes(p.view) ? p.view : 'details',
    sort: LIB_SORTS.includes(p.sort) ? p.sort : 'recorded',
    dir: p.dir === 'asc' ? 'asc' : 'desc',
    group: LIB_GROUPS.includes(p.group) ? p.group : 'none',
    names: p.names === 'file' ? 'file' : 'title',
    badges: p.badges === 'all' ? 'all' : 'smart',
  };
}

// Recordings grouped by the day (or month) they were recorded, like folders that can be folded
const LIB_GROUPS = ['none', 'day', 'month'];

// The name shown in the library: the title, or the file name made from the template
function libTitle(m) {
  return libPrefs().names === 'file' ? formatFileName(m) : (m.title || '');
}

function libDateKey(m, group) {
  const d = String(m.recorded_at || m.created_at || '').slice(0, 10);
  return group === 'month' ? d.slice(0, 7) : d;
}

function libDateLabel(key, group) {
  const lang = state.uiLang === 'en' ? 'en-US' : 'ru-RU';
  const d = new Date(`${group === 'month' ? key + '-01' : key}T12:00:00`);
  if (isNaN(d)) return key || '—';
  if (group === 'month') {
    const s = d.toLocaleDateString(lang, { month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '');
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  const local = n => { const x = new Date(); x.setDate(x.getDate() - n); return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`; };
  const named = key === local(0) ? t('libToday') : key === local(1) ? t('libYesterday') : '';
  const full = d.toLocaleDateString(lang, { day: 'numeric', month: 'long', year: 'numeric', weekday: 'short' }).replace(/\s*г\.$/, '');
  return named ? `${named} · ${full}` : full;
}

// Where the open recording is: its folders (with their parents) and its day/month group, marked by a
// dot on their rows, so it is found even with the groups folded
function libHere() {
  const m = state.activeMeeting;
  const key = m ? `${m.id}:${m.folder_id}:${libPrefs().group}:${m.recorded_at || m.created_at}` : '';
  if (state._libHere && state._libHere.key === key) return state._libHere;
  const folders = new Set(), dates = new Set();
  if (m) {
    const byId = new Map((state.folders || []).map(f => [f.id, f]));
    for (let id = m.folder_id, guard = 0; id != null && byId.has(id) && guard < 50; id = byId.get(id).parent_id, guard++) folders.add(id);
    const group = libPrefs().group;
    if (group !== 'none') dates.add(`${byId.has(m.folder_id) ? m.folder_id : 'root'}:${libDateKey(m, group)}`);
  }
  return state._libHere = { key, folders, dates };
}

// "Fold all" / "Unfold all" in the library header: folders and day groups together
function libAnyOpen() {
  const folders = collapsedFolders();
  const dates = collapsedDates();
  return (state.folders || []).some(f => !folders.has(f.id)) || (state._libDateIds || []).some(id => !dates.has(id));
}

function toggleLibraryFold() {
  const fold = libAnyOpen();
  try {
    localStorage.setItem('verbascribe_folders_collapsed', JSON.stringify(fold ? (state.folders || []).map(f => f.id) : []));
    localStorage.setItem('verbascribe_dates_collapsed', JSON.stringify(fold ? (state._libDateIds || []).slice(-300) : []));
  } catch (e) {}
  renderMeetingsSidebar();
}

function renderLibFoldButton() {
  const btn = document.getElementById('lib-fold-btn');
  if (!btn) return;
  const fold = libAnyOpen();
  btn.title = t(fold ? 'tipLibFoldAll' : 'tipLibUnfoldAll');
  btn.innerHTML = fold ? ICON_FOLD_ALL : ICON_UNFOLD_ALL;
}

function collapsedDates() {
  try { return new Set(JSON.parse(localStorage.getItem('verbascribe_dates_collapsed') || '[]')); } catch (e) { return new Set(); }
}

function toggleDateCollapsed(key) {
  const set = collapsedDates();
  if (set.has(key)) set.delete(key); else set.add(key);
  try { localStorage.setItem('verbascribe_dates_collapsed', JSON.stringify([...set].slice(-300))); } catch (e) {}
  renderMeetingsSidebar();
}

function dateRowHtml(id, label, count, depth, open) {
  return `
    <div data-click="toggleDateCollapsed" data-click-args='[{"$": "data", "k": "id"}]' data-id="${escapeHtml(id)}" tabindex="0" role="button"
         class="flex items-center gap-1.5 pr-2 py-1 rounded-lg cursor-pointer text-apple-secondary hover:bg-white/5 transition"
         style="padding-left:${6 + depth * 14}px" title="${escapeHtml(label)}">
      <span class="shrink-0 text-apple-tertiary transition-transform ${open ? 'rotate-90' : ''}">${ICON_CHEVRON}</span>
      <span class="text-[11px] font-semibold truncate flex-1">${escapeHtml(label)}</span>
      ${!open && libHere().dates.has(id) ? `<span class="lib-here-dot" title="${escapeHtml(t('tipLibHere'))}"></span>` : ''}
      <span class="text-[10.5px] font-mono text-apple-tertiary">${count}</span>
    </div>`;
}

function setLibPref(key, value) {
  const p = { ...libPrefs() };
  // Choosing the sort that is already on flips its direction
  if (key === 'sort' && p.sort === value) p.dir = p.dir === 'asc' ? 'desc' : 'asc';
  else if (key === 'sort') p.dir = value === 'name' ? 'asc' : 'desc';
  if (key !== 'dir') p[key] = value;
  try { localStorage.setItem('verbascribe_library_view', JSON.stringify(p)); } catch (e) {}
  _libPrefsCache = null;
  renderMeetingsSidebar();
}

const _libCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function libComparator(prefs) {
  const { sort, dir } = prefs;
  const byFile = prefs.names === 'file';
  const key = {
    recorded: m => m.recorded_at || m.created_at || '',
    added: m => m.created_at || '',
    name: m => byFile ? formatFileName(m) : (m.title || ''),
    duration: m => Number(m.duration) || 0,
    size: m => Number(m.size_bytes) || 0,
  }[sort];
  const sign = dir === 'asc' ? 1 : -1;
  // Dates are ISO strings: compared as strings; only names need the (slow) locale collation
  const cmp = sort === 'name' ? _libCollator.compare : (x, y) => (x < y ? -1 : x > y ? 1 : 0);
  return (a, b) => {
    const x = key(a), y = key(b);
    const c = typeof x === 'string' ? cmp(x, y) : x - y;
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
    + LIB_SORTS.map(v => item('sort', v, t(`libSort_${v}`), p.sort === v, arrow)).join('')
    + `<div class="my-1 h-px bg-white/10"></div><div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('libGroup'))}</div>`
    + LIB_GROUPS.map(v => item('group', v, t(`libGroup_${v}`), p.group === v)).join('')
    + `<div class="my-1 h-px bg-white/10"></div><div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('libNames'))}</div>`
    + item('names', 'title', t('libNames_title'), p.names === 'title')
    + item('names', 'file', t('libNames_file'), p.names === 'file')
    + `<div class="my-1 h-px bg-white/10"></div><div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('libBadges'))}</div>`
    + item('badges', 'smart', t('libBadges_smart'), p.badges === 'smart')
    + item('badges', 'all', t('libBadges_all'), p.badges === 'all');
  menu.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-k]');
    ev.stopPropagation();
    if (!b) return;
    setLibPref(b.dataset.k, b.dataset.v);
    closeLibViewMenu();
  });
  document.body.appendChild(menu);
  const r = e.currentTarget.getBoundingClientRect();
  menu.style.left = `${Math.min(zpx(r.left), viewW() - 232)}px`;
  menu.style.top = `${zpx(r.bottom) + 4}px`;
}

function closeLibViewMenu() {
  document.getElementById('lib-view-menu')?.remove();
}

document.addEventListener('click', closeLibViewMenu);

function formatSize(bytes) {
  return bytes ? formatBytes(bytes) : '';
}

// What the row shows on its right: the sorted-by value when it is not the date
function libSortValue(m) {
  const { sort } = libPrefs();
  if (sort === 'size') return formatSize(m.size_bytes);
  if (sort === 'added') return shortDate(m.created_at);
  return shortDate(m.recorded_at || m.created_at);
}

// --- Several recordings selected: Ctrl+click adds or removes one, Shift+click takes a range ---
state.multi = new Set();
let _libAnchor = null;

function libIsSelected(m) {
  return state.multi.size ? state.multi.has(m.id) : !!(state.activeMeeting && state.activeMeeting.id === m.id);
}

function libVisibleIds() {
  return [...document.querySelectorAll('#meetings-list [data-mid]')].map(el => Number(el.dataset.mid));
}

function libRowClick(e, id) {
  if (e.ctrlKey || e.metaKey) {
    if (!state.multi.size && state.activeMeeting) state.multi.add(state.activeMeeting.id);
    if (state.multi.has(id)) state.multi.delete(id); else state.multi.add(id);
    _libAnchor = id;
  } else if (e.shiftKey) {
    const ids = libVisibleIds();
    const from = ids.indexOf(_libAnchor ?? (state.activeMeeting && state.activeMeeting.id));
    const to = ids.indexOf(id);
    state.multi = new Set(from < 0 ? [id] : ids.slice(Math.min(from, to), Math.max(from, to) + 1));
    document.getSelection()?.removeAllRanges();
  } else {
    state.multi.clear();
    _libAnchor = id;
    openMeeting(id, true);
    return;
  }
  if (state.multi.size === 1 && state.multi.has(state.activeMeeting && state.activeMeeting.id)) state.multi.clear();
  renderMeetingsSidebar();
  renderLibSelectionBar();
}

function clearLibSelection() {
  if (!state.multi.size) return;
  state.multi.clear();
  renderMeetingsSidebar();
  renderLibSelectionBar();
}

// A bar over the list while several recordings are selected
function renderLibSelectionBar() {
  let bar = document.getElementById('lib-selection-bar');
  const n = state.multi.size;
  if (!n) { bar?.remove(); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'lib-selection-bar';
    bar.className = 'mx-1 mb-1.5 px-2 py-1.5 rounded-lg bg-apple-blue/15 border border-apple-blue/30 flex items-center gap-1.5 text-[11.5px] text-white';
    const list = document.getElementById('meetings-list');
    list.parentNode.insertBefore(bar, list);
  }
  bar.innerHTML = `<span class="flex-1 min-w-0 truncate">${escapeHtml(t('libSelected').replace('{n}', () => n))}</span>
    <button data-click="bulkRename" class="ui-btn ui-btn-subtle h-6 !px-2 text-[11px]" title="${escapeHtml(t('tipBulkRename'))}">${escapeHtml(t('bulkRename'))}</button>
    <button data-click="bulkAutoTitles" class="ui-btn ui-btn-subtle h-6 !px-2 text-[11px]" title="${escapeHtml(t('tipBulkAutoTitle'))}">${escapeHtml(t('bulkAutoTitle'))}</button>
    <button data-click="bulkDelete" class="ui-btn h-6 !px-2 text-[11px] bg-apple-red/80 text-white hover:bg-apple-red">${escapeHtml(t('ctxDelete'))}</button>
    <button data-click="clearLibSelection" class="ui-btn ui-btn-subtle h-6 !px-1.5 text-[11px]" title="${escapeHtml(t('cancel'))} (Esc)">✕</button>`;
}

function libSelectedInOrder() {
  const ids = libVisibleIds().filter(id => state.multi.has(id));
  for (const id of state.multi) if (!ids.includes(id)) ids.push(id);
  return ids.map(id => state.meetings.find(m => m.id === id)).filter(Boolean);
}

async function bulkDelete() {
  const list = libSelectedInOrder();
  if (!list.length) return;
  const choice = await confirmDeleteDialog(list);
  if (!choice) return;
  let failed = 0;
  for (const m of list) {
    const res = await fetch(`/api/meetings/${m.id}${deleteQuery(choice, m)}`, { method: 'DELETE' });
    if (!res.ok) failed++;
    else if (state.activeMeeting && state.activeMeeting.id === m.id) {
      state.activeMeeting = null;
      document.getElementById('workspace-content').classList.add('hidden');
      document.getElementById('workspace-empty').classList.remove('hidden');
    }
  }
  state.multi.clear();
  renderLibSelectionBar();
  await loadMeetingsList();
  showToast(failed ? t('bulkSomeFailed').replace('{n}', () => failed) : t('bulkDeleted').replace('{n}', () => list.length));
}

// One name for all, numbered: "Interview {n}" -> "Interview 1", "Interview 2"…; {title} and {date} too
async function bulkRename() {
  const list = libSelectedInOrder();
  if (!list.length) return;
  const tpl = await askText(t('bulkRenamePrompt'), `${list[0].title} {n}`);
  if (!tpl || !tpl.trim()) return;
  const width = String(list.length).length;
  let failed = 0;
  for (const [i, m] of list.entries()) {
    const d = meetingDate(m);
    const ymd = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const title = tpl.replaceAll('{n}', String(i + 1).padStart(width, '0')).replaceAll('{title}', m.title || '').replaceAll('{date}', ymd).trim();
    const res = await fetch(`/api/meetings/${m.id}/title`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
    });
    if (!res.ok) failed++;
  }
  await loadMeetingsList();
  if (state.activeMeeting && state.multi.has(state.activeMeeting.id)) openMeeting(state.activeMeeting.id);
  if (failed) showToast(t('bulkSomeFailed').replace('{n}', () => failed));
}

// Titles made by the analysis model from each transcript
async function bulkAutoTitles() {
  const list = libSelectedInOrder().filter(m => m.status === 'completed');
  if (!list.length) { showToast(t('bulkNoTranscripts')); return; }
  let failed = 0;
  for (const [i, m] of list.entries()) {
    showToast(t('bulkAutoTitleWorking').replace('{i}', () => i + 1).replace('{n}', () => list.length));
    const res = await fetch(`/api/meetings/${m.id}/title/generate`, { method: 'POST' });
    if (!res.ok) failed++;
    await loadMeetingsList();
  }
  if (state.activeMeeting && state.multi.has(state.activeMeeting.id)) openMeeting(state.activeMeeting.id);
  showToast(failed ? t('bulkSomeFailed').replace('{n}', () => failed) : t('bulkAutoTitleDone'));
}

// Ctrl+A selects recordings only after a click in the library (elsewhere it selects text as usual)
let _libHadClick = false;
document.addEventListener('mousedown', e => { _libHadClick = !!e.target.closest?.('#meetings-list'); }, true);
// ... or after moving into it with the keyboard (Tab onto a recording)
document.addEventListener('focusin', e => { if (e.target.closest?.('#meetings-list')) _libHadClick = true; });

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && state.multi.size && !document.querySelector('.modal-backdrop:not(.hidden)')) clearLibSelection();
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '') || document.activeElement?.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && e.code === 'KeyA' && !typing && _libHadClick
      && !document.querySelector('.modal-backdrop:not(.hidden)')) {
    e.preventDefault();
    state.multi = new Set(libVisibleIds());
    renderMeetingsSidebar();
    renderLibSelectionBar();
  }
  // ↑ / ↓ after a click in the library: the previous or next recording (Enter is not needed: it opens)
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && _libHadClick && !typing && !e.altKey
      && !document.querySelector('.modal-backdrop:not(.hidden)')) {
    const ids = libVisibleIds();
    if (!ids.length) return;
    e.preventDefault();
    const cur = ids.indexOf(state.activeMeeting ? state.activeMeeting.id : -1);
    const next = ids[Math.max(0, Math.min(ids.length - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)))];
    if (next !== undefined && (!state.activeMeeting || next !== state.activeMeeting.id)) {
      state.multi.clear();
      openMeeting(next, true);
      document.querySelector(`#meetings-list [data-mid="${next}"]`)?.scrollIntoView({ block: 'nearest' });
    }
  }
  if (e.key === 'Delete' && state.multi.size && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '')
      && !document.activeElement?.isContentEditable) bulkDelete();
});

// "•••" on a row: the same menu as a right click (shown on hover and on keyboard focus)
function rowMoreButton(m) {
  return `<button data-click="stopThen" data-click-args='[{"$": "event"}, "openRecordingContextMenu", {"$": "event"}, ${Number(m.id) || 0}]' tabindex="-1" aria-label="${escapeHtml(t('tipRecordingMore'))}"
    title="${escapeHtml(t('tipRecordingMore'))}" class="row-more opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 px-1 rounded hover:bg-black/20 text-[11px] shrink-0 transition">•••</button>`;
}

function meetingCompactRowHtml(m, depth) {
  const isSelected = libIsSelected(m);
  const isBusy = !DONE_STATUSES.includes(m.status);
  return `
    <div data-click="libRowClick" data-click-args='[{"$": "event"}, ${m.id}]' data-mid="${m.id}" tabindex="0" role="button" data-contextmenu="openRecordingContextMenu" data-contextmenu-args='[{"$": "event"}, ${m.id}]'
         draggable="true" data-dragstart="libraryDragStart" data-dragstart-args='[{"$": "event"}, "m", ${m.id}]' title="${escapeHtml(m.title)}"
         class="group cursor-pointer pr-2 py-1 rounded-md transition flex items-center gap-2 text-[12px] ${
           isSelected ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'
         }" style="padding-left:${10 + depth * 14}px">
      <span class="shrink-0 ${isSelected ? 'text-white/80' : 'text-apple-tertiary'}">${m.is_video ? ICON_FILE_VIDEO : ICON_FILE_AUDIO}</span>
      <span class="truncate flex-1 min-w-0">${escapeHtml(libTitle(m))}</span>
      <span class="rec-badges shrink-0 ${isSelected ? 'text-white/80' : 'text-apple-secondary'}">${recordingBadgesHtml(m)}</span>
      <span class="font-mono text-[10.5px] shrink-0 ${isSelected ? 'text-white/80' : 'text-apple-tertiary'}">${isBusy ? `${m.progress}%` : libPrefs().sort === 'size' ? formatSize(m.size_bytes) : formatTime(m.duration)}</span>
      ${rowMoreButton(m)}
    </div>`;
}

function meetingTileHtml(m) {
  const isSelected = libIsSelected(m);
  const isBusy = !DONE_STATUSES.includes(m.status);
  const cover = m.is_video
    ? `<img src="/api/meetings/${m.id}/thumb" loading="lazy" alt="" draggable="false" data-error="tileIconFallback" data-error-args='[{"$": "el"}]' class="w-full h-full object-cover" />`
    : `<span class="lib-tile-icon">${ICON_FILE_AUDIO}</span>`;
  return `
    <div data-click="libRowClick" data-click-args='[{"$": "event"}, ${m.id}]' data-mid="${m.id}" tabindex="0" role="button" data-contextmenu="openRecordingContextMenu" data-contextmenu-args='[{"$": "event"}, ${m.id}]'
         draggable="true" data-dragstart="libraryDragStart" data-dragstart-args='[{"$": "event"}, "m", ${m.id}]' title="${escapeHtml(m.title)}"
         class="lib-tile group ${isSelected ? 'lib-tile-on' : ''}">
      <div class="lib-tile-cover">
        ${cover}
        <span class="lib-tile-time">${isBusy ? `${m.progress}%` : formatTime(m.duration)}</span>
        ${isBusy ? `<span class="lib-tile-progress" style="width:${m.progress}%"></span>` : ''}
      </div>
      <div class="px-1.5 pt-1 pb-1.5 min-w-0">
        <div class="text-[11.5px] font-medium leading-tight line-clamp-2">${escapeHtml(libTitle(m))}</div>
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
    <div data-click="toggleFolderCollapsed" data-click-args='[${f.id}]' tabindex="0" role="button" data-contextmenu="openFolderMenu" data-contextmenu-args='[{"$": "event"}, ${f.id}]'
         ${f.system_key ? '' : `draggable="true" data-dragstart="libraryDragStart" data-dragstart-args='[{"$": "event"}, "f", ${f.id}]'`}
         data-dragover="libraryDragOver" data-dragover-args='[{"$": "event"}, ${f.id}]' data-dragleave="libraryDragLeave" data-dragleave-args='[{"$": "event"}]' data-drop="libraryDrop" data-drop-args='[{"$": "event"}, ${f.id}]'
         class="folder-row group flex items-center gap-1.5 pr-2 py-1.5 rounded-lg cursor-pointer text-apple-label hover:bg-white/5 transition"
         style="padding-left:${6 + depth * 14}px" title="${escapeHtml(folderName(f))}">
      <span class="shrink-0 text-apple-tertiary transition-transform ${open ? 'rotate-90' : ''}">${ICON_CHEVRON}</span>
      <span class="shrink-0 ${f.system_key ? 'text-apple-blue' : 'text-apple-secondary'}">${folderIcon(f)}</span>
      <span class="text-[12px] font-semibold truncate flex-1">${escapeHtml(folderName(f))}</span>
      ${f.local_only ? `<span class="shrink-0 text-apple-tertiary" title="${escapeHtml(t('tipLocalOnly'))}">${ICON_LOCK}</span>` : ''}
      ${!open && libHere().folders.has(f.id) ? `<span class="lib-here-dot" title="${escapeHtml(t('tipLibHere'))}"></span>` : ''}
      <span class="text-[10.5px] font-mono text-apple-tertiary">${count}</span>
      <button data-click="openFolderMenu" data-click-args='[{"$": "event"}, ${f.id}]' title="${escapeHtml(t('tipFolderMenu'))}"
        class="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 px-1 rounded hover:bg-black/20 text-xs shrink-0 transition">•••</button>
    </div>`;
}

function renderMeetingsSidebar() {
  const listEl = document.getElementById('meetings-list');

  if (state.globalSearchQuery && state.globalSearchResults) {
    const askHtml = `
      <button data-click="askLibraryAboutSearch" title="${escapeHtml(t('tipLibraryAsk'))}"
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
        <div data-click="openMeeting" data-click-args='[${item.meeting_id}, true]' tabindex="0" role="button"
             title="${escapeHtml(item.title)}"
             class="text-xs font-semibold text-white hover:text-apple-blue cursor-pointer truncate">
          ${escapeHtml(item.title)}
        </div>
        ${(item.hits || []).map(h => `
          <div data-click="openLibraryHit" data-click-args='[${Number(item.meeting_id) || 0}, ${Number(h.start) || 0}]' tabindex="0" role="button"
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
  const datesCollapsed = collapsedDates();
  const dateIds = [];
  const items = (list, depth, groupKey, byDate = prefs.group !== 'none') => {
    if (!list || !list.length) return;
    if (byDate) {
      const buckets = new Map();
      for (const m of list) {
        const k = libDateKey(m, prefs.group);
        if (!buckets.has(k)) buckets.set(k, []);
        buckets.get(k).push(m);
      }
      for (const [k, ms] of buckets) {
        const id = `${groupKey}:${k}`;
        dateIds.push(id);
        const open = !datesCollapsed.has(id);
        parts.push([`d${id}`, dateRowHtml(id, libDateLabel(k, prefs.group), ms.length, depth, open)]);
        if (open) items(ms, depth + 1, `${groupKey}_${k}`, false);
      }
      return;
    }
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
  // The day groups of folded folders are not built: the ones seen so far count for "fold all"
  state._libDateIds = [...new Set([...(state._libDateIds || []), ...dateIds])];
  listEl.classList.toggle('lib-badges-all', prefs.badges === 'all');
  patchKeyedList(listEl, parts);
  renderLibFoldButton();
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
    showToast(t('dropSkipped').replace('{names}', () => skipped));
  }
  if (media.length) handleFileSelect(media);
}

function handleGlobalDrop(e) {
  dropMediaFiles(e);
}

function handleFileDrop(e) {
  dropMediaFiles(e);
}

function requireRecognitionModel() {
  if (document.getElementById('upload-model').value) return true;
  showToast(t('noModelInstalled'), { label: t('settings'), onClick: () => openModal('settings', 'recognition') });
  return false;
}

// Sends files to the server; `source` tells a recording made here from an imported file (they go to
// different folders on disk and in the library). The file dates are sent too: for an imported file
// the date it was last modified is the closest to when the meeting took place. The import window
// (importdlg.js) gives its model, language, speakers and processing; without them the defaults of
// the window's lists are used, and the server takes the processing from the settings.
async function uploadFiles(fileList, opts = {}) {
  if (opts.source !== 'recording' && !requireRecognitionModel()) return;
  const fd = new FormData();
  for (const f of fileList) {
    fd.append('files', f);
    fd.append('last_modified', String(f.lastModified || 0));
  }
  fd.append('model_name', opts.model ?? document.getElementById('upload-model').value);
  fd.append('language', opts.language ?? document.getElementById('upload-language').value);
  fd.append('num_speakers', opts.source === 'recording' ? '0' : String(opts.speakers ?? document.getElementById('upload-speakers').value));
  fd.append('source', opts.source || 'import');
  if (opts.processing) fd.append('processing', JSON.stringify(opts.processing));
  if (opts.title) fd.append('title', opts.title);
  if (opts.folderId != null) fd.append('folder_id', String(opts.folderId));
  const res = await fetch('/api/meetings/upload', { method: 'POST', body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(serverText(data.detail) || `HTTP ${res.status}`);
  return data.created || [];
}

// Files chosen or dropped: into the open import window, into a new one (it asks the processing), or
// straight to the queue with the settings when the user said not to ask
async function handleFileSelect(fileList) {
  if (!fileList || fileList.length === 0) return;
  if (impIsOpen()) { impAddFiles(fileList); return; }
  if (askImportParams()) { openImportDialog({ files: fileList }); return; }
  try {
    const created = await uploadFiles(Array.from(fileList), { source: 'import' });
    document.getElementById('file-input').value = '';
    if (!created || !created.length) return;
    switchAppView('library');
    await loadMeetingsList();
    await selectMeeting(created[0].id, true);
    return created;
  } catch (err) {
    showToast(String(err.message || err));
  }
}

// --- Sidebar width: dragged on its right edge, kept per computer ---
const SIDEBAR_MIN = 240, SIDEBAR_MAX = 560, SIDEBAR_DEFAULT = 312;
const MAIN_MIN = 800;  // the recording toolbar and the player need this much next to the sidebar

function setSidebarWidth(w, save) {
  const aside = document.getElementById('app-sidebar');
  if (!aside) return;
  w = Math.round(Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, Math.min(w, viewW() - MAIN_MIN))));
  aside.style.width = `${w}px`;
  if (save) try { localStorage.setItem('verbascribe_sidebar_width', String(w)); } catch (e) {}
}

(function initSidebarResize() {
  // A smaller window takes the room from the sidebar first (the saved width comes back when it grows)
  window.addEventListener('resize', () => {
    let saved = SIDEBAR_DEFAULT;
    try { saved = Number(localStorage.getItem('verbascribe_sidebar_width')) || SIDEBAR_DEFAULT; } catch (e) {}
    setSidebarWidth(saved, false);
  });
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
    const move = (ev) => setSidebarWidth(zpx(ev.clientX - left), false);
    const up = (ev) => {
      setSidebarWidth(zpx(ev.clientX - left), true);
      handle.classList.remove('dragging');
      document.body.classList.remove('sidebar-dragging');
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
})();

// A thumbnail that could not be read gives way to the video icon
function tileIconFallback(el) {
  el.replaceWith(Object.assign(document.createElement('span'), { className: 'lib-tile-icon', innerHTML: ICON_FILE_VIDEO }));
}

// "Ask the assistant" from the search results: about everything found
function askLibraryAboutSearch() {
  openLibraryAsk(state.globalSearchQuery, { type: 'all' });
}

// A search hit: the library, the recording, the moment
function openLibraryHit(meetingId, sec) {
  switchAppView('library');
  selectMeetingAndSeek(meetingId, sec);
}
