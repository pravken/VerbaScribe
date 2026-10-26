// VerbaScribe page script: the home page (package B, docs/redesign-plan.md). Today as a calendar page, the
// greeting and the line of the day; the latest recordings with a line of their overview; the meetings of
// the working day from the calendar (hours chosen by the user, past midnight for a night shift) — they can
// be recorded by themselves; what is being processed; the user's open tasks. Also the dictation button,
// and the details of the status bar (the hardware block).
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

const HM_PX = 44;  // pixels of an hour on the timeline
const HM_PRESETS = [[9, 18, 'hmPreset918'], [8, 20, 'hmPreset820'], [20, 8, 'hmPresetNight'], [0, 0, 'hmPresetDay']];

const home = { recent: [], recentAt: 0, events: [], eventsAt: 0, calConfigured: true, tasks: [], tasksAt: 0, asked: new Set(), started: new Set() };

function hmSettings() { return (state.system && state.system.settings) || {}; }
function hmNum(key, def) { const n = Number(hmSettings()[key]); return Number.isFinite(n) && hmSettings()[key] !== '' && hmSettings()[key] != null ? n : def; }
function hmOn(key, def) { const v = hmSettings()[key]; return v == null || v === '' ? def : String(v) === 'true'; }
function hmOverrides() { try { return JSON.parse(hmSettings().meet_overrides || '{}') || {}; } catch (e) { return {}; } }
function hmLocale() { return state.uiLang === 'en' ? 'en-GB' : 'ru-RU'; }
function hmPad(n) { return String(n).padStart(2, '0'); }

async function hmSave(values) {
  const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: values }) });
  if (!res.ok) { showError(serverText((await readJson(res)).detail)); return false; }
  if (state.system) state.system.settings = { ...(state.system.settings || {}), ...values };
  return true;
}

// --- The working day: start and end hours; an end not after the start runs past midnight ---
function hmDay() {
  const start = hmNum('work_day_start', 9), end = hmNum('work_day_end', 18);
  const len = end > start ? end - start : end + 24 - start;  // 0 → 24: the whole day
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth(), now.getDate(), start);
  // a shift that began yesterday and has not ended yet is still today's working day
  if (now < from) {
    const prev = new Date(from);
    prev.setDate(prev.getDate() - 1);
    if (now.getTime() < prev.getTime() + len * 3600e3) from.setTime(prev.getTime());
  }
  const to = new Date(from.getTime() + len * 3600e3);
  return { start, end, len, from, to, fit: hmOn('work_day_fit', true) };
}

function hmIso(d) {
  return `${d.getFullYear()}-${hmPad(d.getMonth() + 1)}-${hmPad(d.getDate())}T${hmPad(d.getHours())}:${hmPad(d.getMinutes())}`;
}

// --- Data: refreshed when the page is shown, at most every half minute (events: 5 minutes) ---
async function hmLoad(force) {
  const now = Date.now();
  const jobs = [];
  if (force || now - home.recentAt > 30000) {
    home.recentAt = now;
    jobs.push(fetch('/api/home/recent?limit=6').then(r => (r.ok ? r.json() : null)).then(v => { if (Array.isArray(v)) home.recent = v; }).catch(() => {}));
  }
  if (force || now - home.tasksAt > 30000) {
    home.tasksAt = now;
    jobs.push(fetch('/api/commitments').then(r => (r.ok ? r.json() : null)).then(v => { if (Array.isArray(v)) home.tasks = v; }).catch(() => {}));
  }
  jobs.push(hmLoadEvents(force));
  await Promise.all(jobs);
}

let hmEventsLoading = null;  // one request at a time; a failed one is tried again at the next tick
async function hmLoadEvents(force) {
  if (!force && Date.now() - home.eventsAt < 300000) return;
  if (hmEventsLoading) return hmEventsLoading;
  hmEventsLoading = (async () => {
    const d = hmDay();
    // three hours on each side: meetings a little past the edges stretch the day ("fit")
    const a = new Date(d.from.getTime() - 3 * 3600e3), b = new Date(d.to.getTime() + 3 * 3600e3);
    const data = await fetch(`/api/calendar/day?start=${encodeURIComponent(hmIso(a))}&end=${encodeURIComponent(hmIso(b))}`)
      .then(r => (r.ok ? r.json() : null)).catch(() => null);
    if (!data) return;
    home.eventsAt = Date.now();
    home.calConfigured = !!data.configured;
    home.events = (data.events || []).map(e => ({ ...e, s: new Date(e.start), e: new Date(e.end) }));
  })().finally(() => { hmEventsLoading = null; });
  return hmEventsLoading;
}

function renderHome() {
  hmLoad().then(() => { if (state.appView === 'home') renderHomeNow(); });
  renderHomeNow();
}

function renderHomeNow() {
  const now = new Date();
  const loc = hmLocale();
  document.getElementById('hm-tile-mon').textContent = now.toLocaleDateString(loc, { month: 'short' }).replace('.', '').toUpperCase();
  document.getElementById('hm-tile-day').textContent = String(now.getDate());
  document.getElementById('hm-weekday').textContent = `${now.toLocaleDateString(loc, { weekday: 'long' })} · ${now.getFullYear()}`.toUpperCase();
  const h = now.getHours();
  document.getElementById('hm-greeting').textContent = t(h < 5 ? 'hmNight' : h < 12 ? 'hmMorning' : h < 18 ? 'homeHello' : 'hmEvening');
  document.getElementById('hm-dict-key').textContent = hmSettings().dictation_hotkey || 'Ctrl+Alt+D';
  renderHomeRecent();
  const meetings = renderHomeMeetings();
  const queue = renderHomeQueue();
  const tasks = renderHomeTasks();
  const ahead = meetings.filter(m => m.e > now).length;
  const parts = [];
  if (home.calConfigured) parts.push(`${plural(meetings.length, t('hmMeetingForms'))}${meetings.length ? `, ${t('hmAhead').replace('{n}', () => ahead)}` : ''}`);
  if (queue) parts.push(t('hmLineWork').replace('{n}', () => queue));
  parts.push(plural(tasks, t('hmTaskForms')));
  document.getElementById('hm-line').textContent = parts.join(' · ');
}

// --- The latest recordings ---
function hmWhen(iso) {
  const d = new Date(String(iso || '').replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400e3);
  const time = `${hmPad(d.getHours())}:${hmPad(d.getMinutes())}`;
  if (diff === 0) return `${t('hmToday')} ${time}`;
  if (diff === 1) return `${t('hmYesterday')} ${time}`;
  return d.toLocaleDateString(hmLocale(), { day: 'numeric', month: 'short' });
}

function hmDuration(sec) {
  const m = Math.round((sec || 0) / 60);
  if (!m) return '';
  return m >= 60 ? `${Math.floor(m / 60)} ${t('impHours')} ${m % 60} ${t('impMinutes')}` : `${m} ${t('impMinutes')}`;
}

function renderHomeRecent() {
  const box = document.getElementById('hm-recent');
  // what is being processed now shows its progress (the list of the library is fresher than the page's own copy)
  const live = new Map((state.meetings || []).map(m => [m.id, m]));
  const rows = home.recent.filter(r => live.has(r.id) || !state.meetings.length);
  if (!rows.length) {
    box.innerHTML = `<p class="hm-empty">${escapeHtml(t('homeRecentEmpty'))}</p>`;
    return;
  }
  box.innerHTML = rows.map(r => {
    const m = live.get(r.id) || r;
    const busy = !DONE_STATUSES.includes(m.status);
    const meta = [hmWhen(r.recorded_at), hmDuration(m.duration)].filter(Boolean).join(' · ');
    const icon = r.is_video
      ? '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3"/></svg>'
      : '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg>';
    const line = busy
      ? `<span class="hm-busy"><span class="hm-bar"><span style="--fill: ${Math.max(2, m.progress || 0) / 100}"></span></span>${escapeHtml(t('hmProcessing').replace('{pct}', () => m.progress || 0))}</span>`
      : r.summary ? `<span class="hm-sum"><svg class="hm-ai" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 2l1.9 5.6 5.6 1.9-5.6 1.9L10 17l-1.9-5.6-5.6-1.9 5.6-1.9z"/><path d="M18.5 13l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z"/></svg><span class="truncate">${escapeHtml(r.summary)}</span></span>` : '';
    const tag = r.tasks ? `<span class="hm-tag">${escapeHtml(plural(r.tasks, t('hmTaskForms')))}</span>` : '';
    return `<button ${pageCall('click', 'openMeeting', [r.id])} class="hm-rec">
        <span class="hm-rec-tile ${r.is_video ? 'hm-rec-video' : ''}" aria-hidden="true">${icon}</span>
        <span class="min-w-0 flex flex-col gap-1">
          <span class="flex items-center gap-2 min-w-0"><b class="hm-rec-title">${escapeHtml(m.title || r.title)}</b><span class="hm-rec-meta">${escapeHtml(meta)}</span></span>
          ${line}
        </span>
        <span class="flex items-center gap-1.5">${tag}</span>
      </button>`;
  }).join('');
}

// --- The meetings of the working day ---
// on: it will be recorded; ask: it will be asked about first (an in-person meeting); off: not recorded
function hmMeetingState(ev) {
  const all = hmOn('meet_auto_all', false), askIn = hmOn('meet_ask_inperson', true);
  const ov = hmOverrides()[ev.key];
  if (ov === 'rec') return 'on';
  if (ov === 'skip') return all ? 'skipped' : 'off';
  if (!all) return 'off';
  return askIn && !ev.online ? 'ask' : 'on';
}

function renderHomeMeetings() {
  const d = hmDay();
  document.getElementById('hm-day-range').textContent = d.len === 24 ? t('hmAllDay') : `${hmPad(d.start)}–${hmPad(d.end)}`;
  document.getElementById('hm-cal-off').classList.toggle('hidden', home.calConfigured);
  document.getElementById('hm-cal-on').classList.toggle('hidden', !home.calConfigured);
  // audit 8.7: the working hours only matter for the meetings of a calendar
  document.getElementById('hm-day-btn').closest('.hm-day-wrap')?.classList.toggle('hidden', !home.calConfigured);
  const now = new Date();
  // the shown hours: the day, stretched for meetings that run a little past its edges
  let a = d.from.getTime(), b = d.to.getTime();
  const inDay = home.events.filter(e => e.e.getTime() > a - (d.fit ? 3 * 3600e3 : 0) && e.s.getTime() < b + (d.fit ? 3 * 3600e3 : 0));
  const shown = inDay.filter(e => d.fit || (e.e.getTime() > a && e.s.getTime() < b));
  if (d.fit) for (const e of shown) { a = Math.min(a, Math.floor(e.s.getTime() / 3600e3) * 3600e3); b = Math.max(b, Math.ceil(e.e.getTime() / 3600e3) * 3600e3); }
  const meetings = shown.filter(e => e.e.getTime() > a && e.s.getTime() < b);
  const count = document.getElementById('hm-mt-count');
  count.textContent = String(meetings.length);
  count.classList.toggle('hidden', !meetings.length || !home.calConfigured);

  // the switch "record every meeting" and what it means today
  const all = hmOn('meet_auto_all', false);
  const sw = document.getElementById('hm-auto-sw');
  sw.setAttribute('aria-checked', String(all));
  document.getElementById('hm-auto').classList.toggle('hm-auto-on', all);
  document.getElementById('hm-ask-wrap').classList.toggle('hidden', !all);
  document.getElementById('hm-ask-inperson').checked = hmOn('meet_ask_inperson', true);
  const up = meetings.filter(e => e.e > now);
  const states = up.map(hmMeetingState);
  const rec = states.filter(s => s === 'on').length, ask = states.filter(s => s === 'ask').length;
  document.getElementById('hm-auto-sub').textContent = !all
    ? t('hmAutoOffSub')
    : [t('hmAutoOnSub').replace('{n}', () => rec).replace('{of}', () => up.length), ask ? t('hmAutoAskSub').replace('{n}', () => ask) : ''].filter(Boolean).join(' · ');

  // hours, the meetings on them, the line of "now"
  const hours = [];
  for (let x = a; x <= b; x += 3600e3) hours.push(x);
  const top = (ms) => ((ms - a) / 3600e3) * HM_PX;
  const box = document.getElementById('hm-timeline');
  box.style.height = `${top(b)}px`;
  const html = hours.map(x => `<div class="hm-hour" style="top: ${top(x)}px"><span>${hmPad(new Date(x).getHours())}:00</span><i></i></div>`);
  for (const e of meetings) {
    const s = Math.max(e.s.getTime(), a), en = Math.min(e.e.getTime(), b);
    const hpx = Math.max(26, top(en) - top(s) - 3);
    const past = e.e <= now, live = e.s <= now && now < e.e;
    const st = past ? 'past' : hmMeetingState(e);
    const time = `${hmPad(e.s.getHours())}:${hmPad(e.s.getMinutes())}–${hmPad(e.e.getHours())}:${hmPad(e.e.getMinutes())}`;
    const where = e.online ? t('hmOnline') : (e.location || t('hmInPerson'));
    const who = [where, e.attendees.length ? plural(e.attendees.length, t('hmPeopleForms')) : ''].filter(Boolean).join(' · ');
    let action;
    if (past) {
      const r = e.recording;
      action = r
        ? `<button ${pageCall('click', 'openMeeting', [r.id])} class="hm-mt-link">${escapeHtml(DONE_STATUSES.includes(r.status) ? t('hmRecordingLink') : t('hmProcessing').replace('{pct}', () => r.progress || 0))}</button>`
        : `<span class="hm-mt-none">${escapeHtml(t('hmNoRecording'))}</span>`;
    } else if (hmAutoRec && hmAutoRec.key === e.key && isRecording()) {
      action = `<span class="hm-mt-btn hm-mt-rec"><i></i>${escapeHtml(t('hmRecordingNow'))}</span>`;
    } else if (live && now - e.s > 5 * 60e3 && !isRecording()) {
      // half-way through: not started by itself any more (hmAutoTick), but one click records the rest
      action = `<button ${pageCall('click', 'hmRecordNow', [e.key])} class="hm-mt-btn" title="${escapeHtml(t('hmMeetingTip'))}"><i></i>${escapeHtml(t('hmRecordNow'))}</button>`;
    } else {
      const label = { on: 'hmStateOn', ask: 'hmStateAsk', skipped: 'hmStateSkipped', off: 'hmStateOff' }[st];
      action = `<button ${pageCall('click', 'hmToggleMeeting', [e.key])} class="hm-mt-btn hm-mt-${st}" aria-pressed="${st === 'on'}" title="${escapeHtml(t('hmMeetingTip'))}"><i></i>${escapeHtml(t(label))}</button>`;
    }
    html.push(`<div class="hm-mt hm-mt-k-${past ? 'past' : st}${live ? ' hm-mt-live' : ''}" style="top: ${top(s)}px; height: ${hpx}px">
        <span class="flex-1 min-w-0 flex flex-col gap-px" title="${escapeHtml(`${time} · ${who}`)}">
          <span class="flex items-baseline gap-1.5 min-w-0"><span class="hm-mt-title">${escapeHtml(e.title || t('hmUntitled'))}</span>${hpx < 34 ? `<span class="hm-mt-time">${escapeHtml(time)}</span>` : ''}</span>
          ${hpx >= 34 ? `<span class="hm-mt-sub"><span class="hm-mono">${escapeHtml(time)}</span> · ${escapeHtml(who)}</span>` : ''}
        </span>${action}
      </div>`);
  }
  if (!meetings.length) html.push(`<div class="hm-mt-empty">${escapeHtml(t('hmNoMeetings'))}</div>`);
  if (now.getTime() >= a && now.getTime() <= b) html.push(`<div class="hm-now" style="top: ${top(now.getTime())}px"><span></span></div>`);
  box.innerHTML = html.join('');
  return meetings;
}

// A meeting going on: record the rest of it, stopped with the meeting like an automatic one
async function hmRecordNow(key) {
  const ev = home.events.find(e => e.key === key);
  if (!ev || isRecording() || hmAutoRec) return;
  home.started.add(key);
  hmAutoRec = { key, end: ev.e.getTime() };
  await shellQuickRecord();
  renderHomeNow();
}

async function hmToggleMeeting(key) {
  const ev = home.events.find(e => e.key === key);
  if (!ev) return;
  const ov = hmOverrides();
  const st = hmMeetingState(ev);
  // asking → recorded → left out → recorded …; without "all": recorded ↔ not
  if (st === 'on') { if (hmOn('meet_auto_all', false)) ov[key] = 'skip'; else delete ov[key]; }
  else ov[key] = 'rec';
  if (await hmSave({ meet_overrides: JSON.stringify(hmPruneOverrides(ov)) })) renderHomeNow();
}

// Choices of meetings two days past are of no use any more
function hmPruneOverrides(ov) {
  const old = Date.now() - 2 * 86400e3;
  return Object.fromEntries(Object.entries(ov).filter(([k]) => { const t0 = Date.parse(k.split('|').pop()); return !t0 || t0 > old; }));
}

async function hmToggleAutoAll() {
  if (await hmSave({ meet_auto_all: String(!hmOn('meet_auto_all', false)), meet_overrides: '{}' })) renderHomeNow();
}

async function hmSetAskInPerson(on) {
  if (await hmSave({ meet_ask_inperson: String(!!on) })) renderHomeNow();
}

// --- The working day popover ---
function hmToggleDay(e) {
  e.stopPropagation();
  const pop = document.getElementById('hm-day-pop');
  const open = pop.classList.contains('hidden');
  pop.classList.toggle('hidden', !open);
  document.getElementById('hm-day-btn').setAttribute('aria-expanded', String(open));
  if (open) renderHomeDayPop();
}

function renderHomeDayPop() {
  const d = hmDay();
  const opts = (sel, v) => {
    sel.innerHTML = Array.from({ length: 24 }, (_, h) => `<option value="${h}">${hmPad(h)}:00</option>`).join('');
    sel.value = String(v % 24);
  };
  opts(document.getElementById('hm-day-start'), d.start);
  opts(document.getElementById('hm-day-end'), d.end);
  const night = document.getElementById('hm-overnight');
  night.classList.toggle('hidden', d.end > d.start);
  night.textContent = d.len === 24 ? t('hmAllDayNote') : t('hmOvernight');
  document.getElementById('hm-presets').innerHTML = HM_PRESETS.map(([a, b, label]) => {
    const on = d.start === a && d.end % 24 === b;
    return `<button ${pageCall('click', 'hmPickPreset', [a, b])} class="hm-preset${on ? ' hm-preset-on' : ''}" aria-pressed="${on}">${escapeHtml(t(label))}</button>`;
  }).join('');
  document.getElementById('hm-fit').checked = d.fit;
}

async function hmSetDay() {
  const start = Number(document.getElementById('hm-day-start').value), end = Number(document.getElementById('hm-day-end').value);
  await hmApplyDay(start, end);
}

async function hmPickPreset(start, end) { await hmApplyDay(start, end); }

async function hmApplyDay(start, end) {
  if (!await hmSave({ work_day_start: String(start), work_day_end: String(end) })) return;
  home.eventsAt = 0;
  await hmLoadEvents(true);
  renderHomeDayPop();
  renderHomeNow();
}

async function hmSetFit(on) {
  if (await hmSave({ work_day_fit: String(!!on) })) renderHomeNow();
}

document.addEventListener('click', (e) => {
  const pop = document.getElementById('hm-day-pop');
  if (pop && !pop.classList.contains('hidden') && !e.target.closest('#hm-day-btn')) {
    pop.classList.add('hidden');
    document.getElementById('hm-day-btn').setAttribute('aria-expanded', 'false');
  }
});

// --- In work: the queue of the library ---
function renderHomeQueue() {
  const items = state.queue || [];
  document.getElementById('hm-work-card').classList.toggle('hidden', !items.length);
  document.getElementById('hm-queue').innerHTML = items.slice(0, 4).map(q => {
    const m = (state.meetings || []).find(x => x.id === q.id) || {};
    const run = q.state === 'running';
    const pct = run ? (m.progress ?? q.progress ?? 0) : 0;
    return `<button ${pageCall('click', 'openMeeting', [q.id])} class="hm-q">
        <span class="flex items-center gap-2 min-w-0"><b class="hm-q-title">${escapeHtml(q.title || m.title || '')}</b><span class="${run ? 'hm-q-run' : 'hm-q-wait'}">${escapeHtml(run ? `${queueKindLabel(q)} · ${pct}%` : t('hmWaiting'))}</span></span>
        <span class="hm-bar hm-bar-wide"><span style="--fill: ${run ? Math.max(2, pct) / 100 : 0}"></span></span>
      </button>`;
  }).join('');
  return items.length;
}

// --- The user's open tasks: who is the user — names in the setting my_name (comma separated) ---
function hmMyNames() {
  return String(hmSettings().my_name || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
}

function renderHomeTasks() {
  const names = hmMyNames();
  // the whole name, as on the Tasks page (tkIsMe): «Ан» is not «Анна»
  const mine = names.length ? home.tasks.filter(c => names.includes(String(c.who || '').trim().toLowerCase())) : home.tasks;
  document.getElementById('hm-tasks-title').textContent = t(names.length ? 'hmMyTasks' : 'hmOpenTasks');
  document.getElementById('hm-my-name').classList.toggle('hidden', !!names.length);
  const count = document.getElementById('hm-tasks-count');
  count.textContent = String(mine.length);
  count.classList.toggle('hidden', !mine.length);
  // by the due date (the one set by hand too), as on the Tasks page; the nearest first, then the ones without one
  const list = [...mine].sort((x, y) => (x.due_date ? 0 : 1) - (y.due_date ? 0 : 1)
    || String(x.due_date || '').localeCompare(String(y.due_date || ''))
    || String(y.meeting_date).localeCompare(String(x.meeting_date))).slice(0, 5);
  document.getElementById('hm-tasks').innerHTML = list.length ? list.map(c => `
      <label class="hm-task">
        <input type="checkbox" ${pageCall('change', 'hmTaskDone', [c.id])} />
        <span class="flex-1 min-w-0 flex flex-col gap-0.5"><span class="hm-task-text">${escapeHtml(c.what)}</span>
          <span class="hm-task-sub">${escapeHtml([names.length ? '' : c.who, c.meeting_title].filter(Boolean).join(' · '))}</span></span>
        ${c.due_date ? `<span class="hm-due ${tkDueTone(c)}">${escapeHtml(tkDueText(c))}</span>` : ''}
      </label>`).join('')
    : `<p class="hm-empty">${escapeHtml(t('tasksEmpty'))}</p>`;
  return mine.length;
}

async function hmTaskDone(id) {
  const res = await fetch(`/api/commitments/${Number(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'done' }) });
  if (!res.ok) { showError(serverText((await readJson(res)).detail)); return; }
  home.tasks = home.tasks.filter(c => c.id !== Number(id));
  refreshTasksCount(true);
  renderHomeNow();
  showToast(t('hmTaskDoneToast'), { label: t('hmUndo'), onClick: () => hmTaskUndo(id) });
}

async function hmTaskUndo(id) {
  await fetch(`/api/commitments/${Number(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'open' }) });
  home.tasksAt = 0;
  refreshTasksCount(true);
  await hmLoad(true);
  renderHomeNow();
}

async function hmSetMyName() {
  const v = await askText(t('hmMyNamePrompt'), hmSettings().my_name || '');
  if (v == null) return;
  if (await hmSave({ my_name: v.trim().slice(0, 200) })) renderHomeNow();
}

// --- Recording the meetings of the calendar by themselves (the page is open: it records) ---
// Every 20 s: a meeting that began and is to be recorded starts the recording (once; a recording the user
// stopped is not started again); the recording it started stops a minute after the meeting ends. An
// in-person meeting to be asked about shows the question a minute before it. The window hidden in the
// tray or minimized keeps the page running (as the calls recorded by themselves do, studio.js), so the
// tick runs then too; the browser may slow it to once a minute, which the 5-minute start window allows.
// The question brings the window back (window.pywebview.api.show_window).
let hmAutoRec = null;  // {key, end}: the recording started for a meeting

async function hmAutoTick() {
  await hmLoadEvents(false);
  const now = Date.now();
  if (hmAutoRec && !isRecording() && !_quickRecBusy) hmAutoRec = null;  // the user stopped it: it stays stopped
  if (hmAutoRec && now > hmAutoRec.end + 60e3) {
    const rec = hmAutoRec;
    hmAutoRec = null;
    if (isRecording()) await shellQuickRecord();
    home.started.add(rec.key);
    return;
  }
  for (const e of home.events) {
    const s = e.s.getTime(), en = e.e.getTime();
    if (en <= now || home.started.has(e.key)) continue;
    const st = hmMeetingState(e);
    if (st === 'ask' && now >= s - 60e3 && now < s + 5 * 60e3 && !home.asked.has(e.key)) {
      home.asked.add(e.key);
      hmShowAsk(e);
      continue;
    }
    // only near the start: a meeting half over is not recorded by surprise
    if (st === 'on' && now >= s - 15e3 && now < s + 5 * 60e3 && !isRecording() && !hmAutoRec) {
      home.started.add(e.key);
      hmAutoRec = { key: e.key, end: en };
      await shellQuickRecord();
      showToast(t('hmAutoStarted').replace('{title}', () => e.title || t('hmUntitled')));
    }
  }
}

let hmAskEvent = null;
function hmShowAsk(e) {
  hmAskEvent = e;
  document.getElementById('hm-ask-title').textContent = t('hmAskTitle').replace('{title}', () => e.title || t('hmUntitled'));
  document.getElementById('hm-ask-text').textContent = t('hmAskText').replace('{where}', () => e.location || t('hmInPerson'));
  document.getElementById('hm-ask').classList.remove('hidden');
  const api = window.pywebview && window.pywebview.api;
  if (document.hidden && api && api.show_window) api.show_window().catch(() => {});
}

async function hmAskAnswer(yes) {
  document.getElementById('hm-ask').classList.add('hidden');
  const e = hmAskEvent;
  hmAskEvent = null;
  if (!e || yes == null) return;
  const ov = hmOverrides();
  ov[e.key] = yes ? 'rec' : 'skip';
  await hmSave({ meet_overrides: JSON.stringify(hmPruneOverrides(ov)) });
  if (yes && !isRecording() && !hmAutoRec) {
    home.started.add(e.key);
    hmAutoRec = { key: e.key, end: e.e.getTime() };
    await shellQuickRecord();
  }
  if (state.appView === 'home') renderHomeNow();
}

// --- Dictation from the home page (app/dictation.py, the text is shown here, not typed) ---
let hmDictTimer = null;

async function hmDictToggle() {
  const res = await fetch('/api/dictation/toggle', { method: 'POST' });
  const data = await readJson(res);
  if (!res.ok) { showError(serverText(data.detail)); return; }
  hmDictShow(data.state === 'started' ? 'listen' : 'busy');
  clearInterval(hmDictTimer);
  hmDictTimer = setInterval(hmDictPoll, 500);
}

async function hmDictPoll() {
  const st = await fetch('/api/dictation').then(r => (r.ok ? r.json() : null)).catch(() => null);
  if (!st) return;
  if (st.active) { hmDictShow('listen'); return; }
  if (st.busy) { hmDictShow('busy'); return; }
  clearInterval(hmDictTimer);
  if (st.error && !st.last_text) {
    hmDictHide();
    showToast(serverText(st.error));
    return;
  }
  hmDictShow('done', st.last_text);
  loadMeetingsList();  // the dictation is kept in the library, folder "Dictations"
}

function hmDictShow(mode, text) {
  const bar = document.getElementById('hm-dict');
  bar.classList.remove('hidden');
  bar.dataset.mode = mode;
  document.getElementById('hm-dict-title').textContent = t({ listen: 'hmDictListen', busy: 'hmDictBusy', done: 'hmDictHeard' }[mode]);
  document.getElementById('hm-dict-text').textContent = mode === 'done' ? text : t(mode === 'listen' ? 'hmDictListenSub' : 'hmDictBusySub');
  document.getElementById('hm-dict-lvl').classList.toggle('hidden', mode !== 'listen');
  document.getElementById('hm-dict-done').classList.toggle('hidden', mode !== 'listen');
  document.getElementById('hm-dict-copy').classList.toggle('hidden', mode !== 'done');
  document.getElementById('hm-dict-cancel').textContent = t(mode === 'done' ? 'close' : 'cancel');
  document.getElementById('hm-dict-btn').classList.toggle('hm-qa-on', mode === 'listen');
}

function hmDictHide() {
  document.getElementById('hm-dict').classList.add('hidden');
  document.getElementById('hm-dict-btn').classList.remove('hm-qa-on');
}

async function hmDictCancel() {
  clearInterval(hmDictTimer);
  if (document.getElementById('hm-dict').dataset.mode !== 'done') await fetch('/api/dictation/cancel', { method: 'POST' });
  hmDictHide();
}

async function hmDictCopy() {
  try { await navigator.clipboard.writeText(document.getElementById('hm-dict-text').textContent); showToast(t('copied'), null, 'success'); }
  catch (e) { showError(String(e.message || e)); }
}

// The tray's "Open the last recording" (run.py): the newest one of the library (read first when the
// window was just opened and the list has not come yet)
async function openLastRecording() {
  if (!(state.meetings || []).length) await loadMeetingsList().catch(() => {});
  const last = [...(state.meetings || [])].sort((x, y) =>
    String(y.recorded_at || y.created_at || '').localeCompare(String(x.recorded_at || x.created_at || '')))[0];
  if (last) openMeeting(last.id);
  else switchAppView('home');
}

registerView('home', { show: renderHome, refresh: (changed) => { if (changed) home.recentAt = 0; renderHome(); } });

document.addEventListener('DOMContentLoaded', () => {
  setInterval(hmAutoTick, 20000);
  // the clock line and the processing on the page follow by themselves
  setInterval(() => { if (state.appView === 'home' && !document.hidden) renderHomeNow(); }, 30000);
});
