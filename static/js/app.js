// VerbaScribe page script, part 9 of 9: Updates, start-up, analysis and chapters, export, folders, Obsidian, details, the text menu.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// --- Updates (app/updater.py): check GitHub Releases, download, verify, install on request ---
const updateState = { info: null, notified: false, polling: false };

function renderUpdateStatus() {
  const info = updateState.info;
  const text = document.getElementById('update-status-text');
  const btn = document.getElementById('update-install-btn');
  if (!text || !info) return;
  const dl = info.download || {};
  const ver = () => (info.latest || {}).version || '';
  let msg = t('updInstalled').replace('{v}', () => info.current);
  if (dl.active) msg += ' ' + t('updDownloading').replace('{v}', () => ver()).replace('{pct}', () => dl.progress);
  else if (dl.error) msg += ' ' + dl.error;
  else if (info.available && info.git_checkout) msg += ' ' + t('updGitClone').replace('{v}', () => ver());
  else if (info.available && dl.done) msg += ' ' + t(info.auto ? 'updDownloadedOnExit' : 'updDownloaded').replace('{v}', () => ver());
  else if (info.available) msg += ' ' + t('updAvailable').replace('{v}', () => ver());
  else if (info.error) msg += ' ' + t('updCheckFailed').replace('{why}', () => info.error);
  else if (info.checked_at) msg += ' ' + t('updLatest');
  text.textContent = msg;
  btn.classList.toggle('hidden', !info.can_install || !!dl.active);
  btn.textContent = dl.done ? t('updateRestartBtn') : t('updateInstallBtn');
}

async function checkForUpdates(force) {
  try {
    const r = force ? await fetch('/api/update/check', { method: 'POST' }) : await fetch('/api/update?check=true');
    updateState.info = await r.json();
  } catch (e) { return; }
  renderUpdateStatus();
  const info = updateState.info;
  if (info.available && info.auto && (info.download || {}).active) {
    followUpdateDownload();  // started by the server (automatic updates): tell when it is ready
  } else if (!force && info.available && !updateState.notified) {
    updateState.notified = true;
    showToast(t('updToastAvailable').replace('{v}', () => info.latest.version),
      { label: t('updDetails'), onClick: () => openModal('settings', 'general') });
  } else if (force && !info.available && !info.error) {
    showToast(t('updToastLatest'));
  }
}

// Automatic updates: the download runs on the server; when it is ready the user may restart now
// (otherwise it is installed when the app closes)
async function followUpdateDownload() {
  if (updateState.polling) return;
  updateState.polling = true;
  while (true) {
    await new Promise(res => setTimeout(res, 1500));
    try { updateState.info = await (await fetch('/api/update')).json(); } catch (e) { continue; }
    renderUpdateStatus();
    if (!updateState.info.download.active) break;
  }
  updateState.polling = false;
  const info = updateState.info;
  if (info.download.done && !updateState.notified) {
    updateState.notified = true;
    showToast(t('updToastReady').replace('{v}', () => info.latest.version),
      { label: t('updRestartNow'), onClick: () => installUpdate() });
  }
}

async function installUpdate() {
  const info = updateState.info;
  if (!info || !info.can_install) return;
  if (!(info.download || {}).done) {
    if (updateState.polling) return;
    const r = await fetch('/api/update/download', { method: 'POST' });
    if (!r.ok) { showToast((await r.json()).detail || 'Error'); return; }
    updateState.info = await r.json();
    renderUpdateStatus();
    updateState.polling = true;
    while (true) {
      await new Promise(res => setTimeout(res, 700));
      try { updateState.info = await (await fetch('/api/update')).json(); } catch (e) { continue; }
      renderUpdateStatus();
      if (!updateState.info.download.active) break;
    }
    updateState.polling = false;
    if (!updateState.info.download.done) return;
  }
  const ok = await askConfirm(t('updInstallConfirm').replace('{v}', () => info.latest.version));
  if (!ok) return;
  const r = await fetch('/api/update/install', { method: 'POST' });
  if (!r.ok) { showToast(errorText(await readJson(r), r)); return; }
  showToast(t('updInstalling'));
}

document.addEventListener('DOMContentLoaded', () => {
  state.autoScroll = true;
  loadSummaryTemplates();
  initLogoEasterEgg();
  setUILanguage(state.uiLang);
  updateVolumeUI();

  const player = document.getElementById('media-player');
  player.volume = state.volume / 100;
  const waveCanvas = document.getElementById('player-waveform-canvas');

  if (waveCanvas) {
    let isDraggingWave = false;
    const seekFromMouse = (evt) => {
      const rect = waveCanvas.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (evt.clientX - rect.left) / Math.max(1, rect.width)));
      const dur = player.duration || (state.activeMeeting && state.activeMeeting.duration) || 0;
      if (dur > 0) {
        player.currentTime = ratio * dur;
        renderPlayerWaveform(ratio);
      }
    };

    waveCanvas.addEventListener('mousedown', (evt) => {
      isDraggingWave = true;
      seekFromMouse(evt);
    });
    window.addEventListener('mousemove', (evt) => {
      if (isDraggingWave) {
        seekFromMouse(evt);
      }
    });
    window.addEventListener('mouseup', () => {
      isDraggingWave = false;
    });
    waveCanvas.addEventListener('mousemove', (evt) => {
      const rect = waveCanvas.getBoundingClientRect();
      state.playerHoverRatio = Math.max(0, Math.min(1, (evt.clientX - rect.left) / Math.max(1, rect.width)));
      const dur = player.duration || (state.activeMeeting && state.activeMeeting.duration) || 1;
      renderPlayerWaveform((player.currentTime || 0) / dur);
    });
    waveCanvas.addEventListener('mouseleave', () => {
      state.playerHoverRatio = -1;
      const dur = player.duration || (state.activeMeeting && state.activeMeeting.duration) || 1;
      renderPlayerWaveform((player.currentTime || 0) / dur);
    });
    window.addEventListener('resize', () => {
      const dur = player.duration || (state.activeMeeting && state.activeMeeting.duration) || 1;
      renderPlayerWaveform((player.currentTime || 0) / dur);
    });
  }

  player.addEventListener('play', () => {
    document.getElementById('icon-play').classList.add('hidden');
    document.getElementById('icon-pause').classList.remove('hidden');
  });
  player.addEventListener('pause', () => {
    document.getElementById('icon-play').classList.remove('hidden');
    document.getElementById('icon-pause').classList.add('hidden');
  });
  player.addEventListener('loadedmetadata', () => {
    if (player.duration && isFinite(player.duration)) {
      document.getElementById('player-total-time').textContent = formatTime(player.duration);
    }
    state.hasVideo = player.videoWidth > 0;
    if (state.theatre && !state.hasVideo) exitTheatre();  // another recording without a picture
    applyVideoLayout();
  });
  player.addEventListener('loadstart', () => {
    // A new recording: hide video controls until we know it has a video track
    state.hasVideo = false;
    if (document.pictureInPictureElement === player) document.exitPictureInPicture().catch(() => {});
    applyVideoLayout();
  });
  player.addEventListener('enterpictureinpicture', applyVideoLayout);
  player.addEventListener('leavepictureinpicture', applyVideoLayout);
  player.addEventListener('timeupdate', () => {
    const cur = player.currentTime;
    const dur = player.duration || (state.activeMeeting && state.activeMeeting.duration) || 1;
    document.getElementById('player-current-time').textContent = formatTime(cur);
    renderPlayerWaveform(Math.min(1, cur / dur));

    const follow = state.autoScroll !== false && !player.paused;
    const activeSegEl = updatePlaybackHighlight(cur, follow);
    updateTheatreCaption(cur);
    updateActiveChapter(cur);

    if (activeSegEl && follow) {
      const container = document.getElementById('transcript-container');
      const rect = activeSegEl.getBoundingClientRect();
      const cRect = container.getBoundingClientRect();
      if (rect.top < cRect.top + 50 || rect.bottom > cRect.bottom - 50) {
        activeSegEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  });

  loadSystemInfo();
  loadSeries();  // the series name in the context menu of a recording
  loadLlmStatus();
  setTimeout(() => checkForUpdates(false), 4000);  // after the page has settled; only if enabled in settings
  loadDictionary();
  loadVram();
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    scheduleVramRefresh(100);
    loadMeetingsList().then(loadQueue).catch(console.error);  // what changed while the window was in the tray
  });
  startLiveUpdates();
  loadQueue().catch(console.error);
  pollMeetingsLoop();
});

// --- Summaries ---
function selectSummaryTab(tab) {
  state.activeSummaryTab = tab;
  document.querySelectorAll('[id^="sumtab-"]:not(#sumtab-user-templates)').forEach(btn => {
    const k = btn.dataset.tab || btn.id.slice('sumtab-'.length);
    const hidden = btn.classList.contains('hidden');
    btn.className = (k === tab ? 'apple-segment-btn apple-segment-active' : 'apple-segment-btn text-apple-secondary hover:text-white')
      + (hidden ? ' hidden' : '');
  });
  // "My notes" is the user's own text: edited here, nothing to generate
  document.getElementById('generate-summary-btn')?.classList.toggle('hidden', tab === 'mynotes');
  document.getElementById('generate-summary-btn')?.removeAttribute('disabled');
  renderSummaryPanel();
}

// --- The user's summary templates: tabs after the built-in ones ---
async function loadSummaryTemplates() {
  state.summaryTemplates = await fetch('/api/summary-templates').then(r => r.json()).catch(() => []);
  renderTemplateTabs();
}

function renderTemplateTabs() {
  const box = document.getElementById('sumtab-user-templates');
  if (!box) return;
  box.innerHTML = (state.summaryTemplates || []).map(tpl => `<button data-click="selectSummaryTab" data-click-args='[{"$": "data", "k": "tab"}]' id="sumtab-tpl-${escapeHtml(tpl.id)}"
      data-tab="tpl:${escapeHtml(tpl.id)}" title="${escapeHtml(tpl.prompt)}"
      class="apple-segment-btn text-apple-secondary hover:text-white">${escapeHtml(tpl.name)}</button>`).join('');
  selectSummaryTab(state.activeSummaryTab);
}

// Ready-made starting points for the user's own templates
const TEMPLATE_PRESETS = {
  ru: [
    { name: '1:1', prompt: 'Это встреча один на один руководителя и сотрудника. Выдели: как дела у сотрудника и что его беспокоит, обратную связь в обе стороны, договорённости и следующие шаги (кто — что — когда), темы на следующую встречу.' },
    { name: 'Интервью', prompt: 'Это интервью. Составь портрет собеседника, выпиши ключевые ответы на каждый вопрос с цитатами, сильные стороны и сомнения, и вывод: что важно запомнить.' },
    { name: 'Планёрка', prompt: 'Это планёрка команды. По каждому участнику: что сделано, что в работе, какие блокеры. Отдельно — решения и новые задачи с ответственными и сроками.' },
    { name: 'Продажи', prompt: 'Это разговор с клиентом. Выдели: потребности и боли клиента, возражения и как на них ответили, бюджет и сроки, кто принимает решение, договорённости и следующий шаг.' },
  ],
  en: [
    { name: '1:1', prompt: 'This is a one-on-one between a manager and a report. Cover: how the person is doing and what worries them, feedback both ways, agreements and next steps (who — what — when), topics for next time.' },
    { name: 'Interview', prompt: 'This is an interview. Profile the person, list the key answers to each question with quotes, strengths and doubts, and a conclusion: what to remember.' },
    { name: 'Stand-up', prompt: 'This is a team stand-up. For each person: done, in progress, blockers. Then the decisions and new tasks with owners and deadlines.' },
    { name: 'Sales call', prompt: 'This is a call with a customer. Cover: needs and pains, objections and how they were answered, budget and timing, who decides, agreements and the next step.' },
  ],
};

// --- Chapters: "- [MM:SS] Title — description" lines of the "chapters" analysis ---
function parseChapters(md) {
  const out = [];
  for (const raw of String(md || '').split('\n')) {
    const line = raw.replace(/\*\*|__/g, '');  // models sometimes bold the time stamp
    const m = line.match(/^\s*(?:[-*\d.]+\s*)?\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*[-–—:]?\s*(.+?)\s*$/);
    if (!m) continue;
    const secs = m[3] !== undefined ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) : Number(m[1]) * 60 + Number(m[2]);
    const [title, ...rest] = m[4].split(/\s+[—–-]\s+/);
    out.push({ start: secs, title: title.trim(), desc: rest.join(' — ').trim() });
  }
  return out.sort((a, b) => a.start - b.start);
}

let _chapters = [];
let _activeChapter = -1;

function renderChaptersBar() {
  const m = state.activeMeeting;
  _chapters = parseChapters(m && m.summaries && m.summaries.chapters);
  _activeChapter = -1;
  const bar = document.getElementById('chapters-bar');
  bar.classList.toggle('hidden', _chapters.length < 2);
  document.getElementById('chapters-list').innerHTML = _chapters.map((c, i) =>
    `<button data-click="seekPlayer" data-click-args='[${Number(c.start) || 0}]' id="chapter-${i}" title="${escapeHtml(c.desc || c.title)}"
      class="chapter-chip"><span class="font-mono text-apple-tertiary">${formatTime(c.start)}</span> ${escapeHtml(c.title)}</button>`
  ).join('');
}

function updateActiveChapter(cur) {
  if (!_chapters.length) return;
  let idx = -1;
  for (let i = 0; i < _chapters.length; i++) if (_chapters[i].start <= cur + 0.25) idx = i;
  if (idx === _activeChapter) return;
  document.getElementById(`chapter-${_activeChapter}`)?.classList.remove('chapter-chip-active');
  const el = document.getElementById(`chapter-${idx}`);
  el?.classList.add('chapter-chip-active');
  el?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  _activeChapter = idx;
}

// "Clear": what the open tab of Analysis holds (an analysis, my notes, the promises)
function setClearSummaryVisible(on) {
  document.getElementById('clear-summary-btn')?.classList.toggle('hidden', !on);
}

async function clearSummaryTab() {
  const m = state.activeMeeting;
  if (!m) return;
  const tab = state.activeSummaryTab;
  const label = document.getElementById(`sumtab-${tab.startsWith('tpl:') ? 'tpl-' + tab.slice(4) : tab}`)?.textContent.trim() || t('tabSummary');
  if (!(await confirmDialog(t('clearTabTitle').replace('{tab}', () => label), t('clearTabText'), t('clearBtn')))) return;
  let res;
  if (tab === 'mynotes') {
    res = await fetch(`/api/meetings/${m.id}/notes`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: '' }) });
    if (res.ok) m.user_notes = '';
  } else if (tab === 'commitments') {
    res = await fetch(`/api/meetings/${m.id}/commitments`, { method: 'DELETE' });
  } else {
    res = await fetch(`/api/meetings/${m.id}/summaries?key=${encodeURIComponent(tab)}`, { method: 'DELETE' });
    if (res.ok) state.activeMeeting.summaries = (await readJson(res)).summaries || {};
  }
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
  if (tab === 'chapters') renderChaptersBar();
  renderSummaryPanel();
}

function renderSummaryPanel() {
  const box = document.getElementById('summary-content');
  if (!state.activeMeeting) return;
  const tab = state.activeSummaryTab;
  setClearSummaryVisible(tab === 'mynotes' ? !!(state.activeMeeting.user_notes || '').trim()
    : tab !== 'commitments' && !!((state.activeMeeting.summaries || {})[tab] || '').trim());
  if (state.activeSummaryTab === 'mynotes') { renderMyNotesEditor(box); return; }
  if (!aiAvailable()) { renderNoModelCard(box); return; }
  if (state.activeSummaryTab === 'commitments') { renderCommitments(box); return; }
  const summaries = state.activeMeeting.summaries || {};
  const md = summaries[state.activeSummaryTab] || '';

  if (!md) {
    box.innerHTML = `<div class="py-12 text-center text-xs text-apple-secondary">
      ${t('emptySummary')}
      ${summaries.error ? `<div class="mt-2 text-apple-red">${escapeHtml(summaries.error)}</div>` : ''}
    </div>`;
    return;
  }

  renderMarkdownWithTimestamps(box, md, state.activeMeeting.duration);
  if (state.activeSummaryTab === 'followup') {
    const bar = document.createElement('div');
    bar.className = 'mt-4 flex justify-end';
    bar.innerHTML = `<button class="ui-btn ui-btn-primary">${escapeHtml(t('followupOpenMail'))}</button>`;
    bar.querySelector('button').onclick = () => openFollowupMail(md);
    box.appendChild(bar);
  }
}

// The follow-up draft in the mail app: "Тема: …" becomes the subject, the rest the body
function openFollowupMail(md) {
  const text = String(md || '').replace(/\*\*|__|^#+\s*/gm, '').trim();
  const m = text.match(/^(?:Тема|Subject)\s*:\s*(.+)$/im);
  const subject = m ? m[1].trim() : (state.activeMeeting && state.activeMeeting.title) || '';
  const body = (m ? text.replace(m[0], '') : text).trim();
  openExternalMail(`mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body.slice(0, 1800))}`);
}

// The editor of the user's templates: name + what the summary should contain
function openTemplatesEditor() {
  document.getElementById('templates-editor')?.remove();
  let items = (state.summaryTemplates || []).map(x => ({ ...x }));
  const wrap = document.createElement('div');
  wrap.id = 'templates-editor';
  wrap.className = 'fixed inset-0 modal-backdrop z-[60] flex items-center justify-center p-4';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  const presets = TEMPLATE_PRESETS[state.uiLang === 'en' ? 'en' : 'ru'];
  const draw = () => {
    wrap.innerHTML = `<div class="w-full max-w-2xl max-h-[85vh] flex flex-col bg-apple-card border apple-border rounded-2xl shadow-2xl modal-card">
      <div class="px-5 pt-5 pb-3 border-b apple-border">
        <div class="text-sm font-semibold text-white">${escapeHtml(t('templatesTitle'))}</div>
        <p class="text-[11px] text-apple-secondary mt-1">${escapeHtml(t('templatesDesc'))}</p>
      </div>
      <div class="flex-1 overflow-y-auto px-5 py-3 space-y-3">
        ${items.map((x, i) => `<div class="p-3 rounded-xl bg-apple-sidebar border border-white/10 space-y-2">
          <div class="flex items-center gap-2">
            <input data-i="${i}" data-f="name" value="${escapeHtml(x.name)}" maxlength="60" placeholder="${escapeHtml(t('templateName'))}"
              class="flex-1 h-8 bg-apple-card border apple-border rounded-lg px-2.5 text-xs text-white focus:outline-none focus:border-apple-blue" />
            <button data-del="${i}" class="ui-btn ui-btn-subtle px-2 hover:text-apple-red" title="${escapeHtml(t('ctxDelete'))}">${SVG_ICONS.trash}</button>
          </div>
          <textarea data-i="${i}" data-f="prompt" rows="3" maxlength="4000" placeholder="${escapeHtml(t('templatePrompt'))}"
            class="w-full bg-apple-card border apple-border rounded-lg px-2.5 py-2 text-xs text-white focus:outline-none focus:border-apple-blue resize-y">${escapeHtml(x.prompt)}</textarea>
        </div>`).join('') || `<div class="py-6 text-center text-xs text-apple-tertiary">${escapeHtml(t('templatesEmpty'))}</div>`}
        <div class="flex flex-wrap items-center gap-1.5 pt-1">
          <span class="text-[11px] text-apple-secondary">${escapeHtml(t('templatesAddFrom'))}</span>
          ${presets.map((p, i) => `<button data-preset="${i}" class="ui-btn ui-btn-subtle h-7 text-[11px]">${escapeHtml(p.name)}</button>`).join('')}
          <button data-add="1" class="ui-btn ui-btn-subtle h-7 text-[11px]">+ ${escapeHtml(t('templateBlank'))}</button>
        </div>
      </div>
      <div class="px-5 py-3 border-t apple-border flex justify-end gap-2">
        <button data-a="no" class="ui-btn ui-btn-subtle">${escapeHtml(t('cancel'))}</button>
        <button data-a="save" class="ui-btn ui-btn-primary">${escapeHtml(t('save'))}</button>
      </div></div>`;
  };
  draw();
  wrap.addEventListener('input', e => {
    const el = e.target.closest('[data-f]');
    if (el) items[Number(el.dataset.i)][el.dataset.f] = el.value;
  });
  wrap.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (e.target === wrap || (b && b.dataset.a === 'no')) { wrap.remove(); return; }
    if (!b) return;
    if (b.dataset.del !== undefined) { items.splice(Number(b.dataset.del), 1); draw(); }
    else if (b.dataset.preset !== undefined) { items.push({ ...presets[Number(b.dataset.preset)], id: '' }); draw(); }
    else if (b.dataset.add) { items.push({ id: '', name: '', prompt: '' }); draw(); }
    else if (b.dataset.a === 'save') {
      const res = await fetch('/api/summary-templates', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ templates: items }) });
      if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
      state.summaryTemplates = await readJson(res);
      renderTemplateTabs();
      wrap.remove();
    }
  });
  document.body.appendChild(wrap);
}

// --- Commitments (app/commitments.py): who promised what to whom, by when ---
function commitmentRowHtml(c, withMeeting) {
  const who = escapeHtml(c.who || '—') + (c.to_whom ? ` <span class="text-apple-tertiary">→ ${escapeHtml(c.to_whom)}</span>` : '');
  const ts = c.at != null ? ` <button data-mid="${c.meeting_id}" data-at="${c.at}" data-click="openCommitmentPlace" data-click-args='[{"$": "el"}]' class="font-mono text-[11px] text-apple-blue hover:underline">[${formatTime(c.at)}]</button>` : '';
  const where = withMeeting ? `<div class="text-[10.5px] text-apple-tertiary mt-0.5">${escapeHtml(c.meeting_title || '')} · ${escapeHtml(c.meeting_date || '')}</div>` : '';
  return `<label class="flex items-start gap-2.5 py-2 border-b border-white/5 cursor-pointer ${c.status === 'done' ? 'opacity-50' : ''}">
      <input type="checkbox" ${c.status === 'done' ? 'checked' : ''} data-cid="${c.id}" data-change="toggleCommitment" data-change-args='[{"$": "el"}]' class="mt-0.5 rounded bg-apple-sidebar border-white/20 text-apple-blue" />
      <span class="min-w-0 text-[13px] text-apple-label leading-snug"><span class="font-medium text-white">${who}</span>: ${escapeHtml(c.what)}${c.due ? ` <span class="text-apple-amber text-[11.5px]">(${escapeHtml(c.due)})</span>` : ''}${ts}${where}</span>
    </label>`;
}

async function renderCommitments(box) {
  const m = state.activeMeeting;
  box.innerHTML = `<div class="py-6 text-center text-xs text-apple-secondary animate-pulse">…</div>`;
  const items = await fetch(`/api/meetings/${m.id}/commitments`).then(r => r.json()).catch(() => []);
  if (state.activeMeeting !== m || state.activeSummaryTab !== 'commitments') return;
  setClearSummaryVisible(items.length > 0);
  box.innerHTML = items.length
    ? `<div>${items.map(c => commitmentRowHtml(c, false)).join('')}</div>${commitmentExportBar(m.id)}`
    : `<div class="py-12 text-center text-xs text-apple-secondary">${escapeHtml(t('commitmentsEmpty'))}</div>`;
}

async function extractCommitments() {
  const m = state.activeMeeting;
  const btn = document.getElementById('generate-summary-btn');
  const box = document.getElementById('summary-content');
  const { chosenModel, prettyModel } = summaryModelChoice();
  btn.disabled = true;
  box.innerHTML = `<div class="py-12 flex items-center justify-center gap-2 text-xs text-apple-secondary animate-pulse"><span>${escapeHtml(t('commitmentsWorking'))}</span><span class="px-2 py-0.5 rounded-md bg-white/10 text-white font-medium">${escapeHtml(prettyModel)}</span></div>`;
  try {
    const res = await fetch(`/api/meetings/${m.id}/commitments`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: chosenModel }) });
    const data = await readJson(res);
    if (!res.ok) throw new Error(data.detail || 'Error');
    if (state.activeMeeting === m) renderCommitments(box);
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

// Promises as tasks for another app: Markdown (Obsidian, Notion), Todoist's CSV, a table
function commitmentExportBar(meetingId) {
  const b = (fmt, label) => `<button data-click="exportCommitments" data-click-args='[${jsArg(fmt)}, ${jsArg(meetingId)}]' class="ui-btn ui-btn-subtle h-7 text-[11px]">${escapeHtml(label)}</button>`;
  return `<div class="flex flex-wrap items-center gap-1.5 pt-3">
      <span class="text-[11px] text-apple-tertiary mr-1">${escapeHtml(t('commitmentsExport'))}</span>
      ${b('md', 'Markdown')}${b('todoist', 'Todoist')}${b('csv', t('commitmentsTable'))}
    </div>`;
}

async function exportCommitments(fmt, meetingId) {
  const res = await fetch('/api/commitments/export', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fmt, meeting_id: meetingId, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  if (data.ok) showToast(t('commitmentsSaved'), { label: t('modelReveal'), onClick: () => revealPath(data.path) });
}

async function toggleCommitment(input) {
  const res = await fetch(`/api/commitments/${input.dataset.cid}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: input.checked ? 'done' : 'open' }),
  });
  if (!res.ok) { input.checked = !input.checked; showToast((await readJson(res)).detail || 'Error'); return; }
  input.closest('label')?.classList.toggle('opacity-50', input.checked);
}

async function openCommitmentPlace(btn) {
  document.getElementById('open-commitments')?.remove();
  switchAppView('library');
  await selectMeetingAndSeek(Number(btn.dataset.mid), Number(btn.dataset.at));
}

// All open promises of the library, from the assistant's side panel
async function openOpenCommitments() {
  const items = await fetch('/api/commitments').then(r => r.json()).catch(() => []);
  document.getElementById('open-commitments')?.remove();
  const wrap = document.createElement('div');
  wrap.id = 'open-commitments';
  wrap.className = 'fixed inset-0 modal-backdrop z-[60] flex items-center justify-center p-4';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.innerHTML = `<div class="w-full max-w-2xl max-h-[85vh] flex flex-col bg-apple-card border apple-border rounded-2xl shadow-2xl modal-card">
      <div class="px-5 pt-5 pb-3 border-b apple-border flex items-center justify-between gap-3">
        <div><div class="text-sm font-semibold text-white">${escapeHtml(t('openCommitmentsBtn'))}</div>
          <p class="text-[11px] text-apple-secondary mt-1">${escapeHtml(t('openCommitmentsDesc'))}</p></div>
        <button data-a="close" class="ui-btn ui-btn-subtle">${escapeHtml(t('close'))}</button>
      </div>
      <div class="flex-1 overflow-y-auto px-5 py-2">${items.length ? items.map(c => commitmentRowHtml(c, true)).join('') + commitmentExportBar(null)
        : `<div class="py-10 text-center text-xs text-apple-tertiary">${escapeHtml(t('commitmentsNoneOpen'))}</div>`}</div>
    </div>`;
  wrap.addEventListener('click', e => { if (e.target === wrap || e.target.closest('[data-a="close"]')) wrap.remove(); });
  document.body.appendChild(wrap);
}

// An analysis model is there: a local one downloaded in Ollama, or a cloud one with its key
function aiAvailable() {
  const ollamaModels = ((state.lastOllama && state.lastOllama.models) || []).length;
  const st = state.llm;
  const cloud = st && st.consent && st.providers && st.providers[st.provider] && st.providers[st.provider].key && st.providers[st.provider].key.saved;
  return state.lastOllama === undefined || ollamaModels > 0 || !!cloud;  // not loaded yet: assume yes
}

// Instead of buttons that only fail when clicked: what to install, in one place
function renderNoModelCard(box) {
  document.getElementById('generate-summary-btn')?.setAttribute('disabled', '');
  box.innerHTML = `<div class="max-w-md mx-auto py-6 text-center space-y-3">
      <div class="text-sm font-semibold text-white">${escapeHtml(t('noAiTitle'))}</div>
      <p class="text-[12px] text-apple-secondary leading-relaxed">${escapeHtml(t('noAiText'))}</p>
      <div class="flex justify-center gap-2 pt-1">
        <button data-click="pullFirstRunLlm" class="ui-btn ui-btn-primary">${escapeHtml(t('noAiLocal'))}</button>
        <button data-click="openModal" data-click-args='["settings", "analysis"]' class="ui-btn ui-btn-subtle">${escapeHtml(t('noAiCloud'))}</button>
      </div>
    </div>`;
}

// --- My notes of a recording: plain text, saved as it is typed; every summary takes it in ---
function renderMyNotesEditor(box) {
  const m = state.activeMeeting;
  box.innerHTML = `<div class="space-y-2">
      <p class="text-[11px] text-apple-tertiary">${escapeHtml(t('notesHint'))}</p>
      <textarea id="my-notes-input" rows="14" placeholder="${escapeHtml(t('notesPh'))}"
        class="w-full bg-apple-sidebar border apple-border rounded-xl px-4 py-3 text-[13px] leading-relaxed text-white placeholder-apple-tertiary focus:outline-none focus:border-apple-blue resize-y"></textarea>
      <div id="my-notes-status" class="text-[10.5px] text-apple-tertiary h-4"></div>
    </div>`;
  const ta = document.getElementById('my-notes-input');
  ta.value = m.user_notes || '';
  ta.oninput = () => {
    const id = m.id, text = ta.value;
    m.user_notes = text;
    setClearSummaryVisible(!!text.trim());
    clearTimeout(window._notesSaveTimer);
    document.getElementById('my-notes-status').textContent = '…';
    window._notesSaveTimer = setTimeout(async () => {
      const res = await fetch(`/api/meetings/${id}/notes`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: text }) });
      const st = document.getElementById('my-notes-status');
      if (st) st.textContent = res.ok ? t('notesSaved') : ((await readJson(res)).detail || 'Error');
    }, 600);
  };
}

// LLM output is derived from the recording's speech, so it is treated as untrusted HTML
function sanitizedMarkdown(md) {
  return DOMPurify.sanitize(marked.parse(md || ''), { FORBID_TAGS: ['style', 'form', 'input'], FORBID_ATTR: ['style'] });
}

function renderMarkdownWithTimestamps(box, md, durationSec) {
  box.innerHTML = sanitizedMarkdown(md);
  linkifyTimestamps(box, Number(durationSec) || 0, (sec) => seekPlayer(sec));
  markUnverified(box);
}

// "[?]" (app/grounding.py): a point of a summary the transcript does not support
function markUnverified(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.nodeValue.includes('[?]')) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const parts = node.nodeValue.split('[?]');
    const frag = document.createDocumentFragment();
    parts.forEach((p, i) => {
      if (p) frag.appendChild(document.createTextNode(p));
      if (i < parts.length - 1) {
        const b = document.createElement('span');
        b.className = 'ml-1 px-1.5 py-px rounded bg-apple-amber/15 text-apple-amber text-[10.5px] font-medium whitespace-nowrap align-middle';
        b.textContent = t('unverifiedBadge');
        b.title = t('tipUnverified');
        frag.appendChild(b);
        b.closest('li, p')?.classList.add('opacity-80');
      }
    });
    node.replaceWith(frag);
  }
}

// Time stamps in model answers come in many shapes: [03:14], [03:14-05:06], (1:02:03), "03:14 — …"
// at the start of a line, inside `code`. Each one becomes a button that seeks there. Text nodes are
// rewritten (never the HTML string), so attributes and entities are left alone. A bare "10:30" in
// the middle of a sentence is more likely a clock time and stays text, as does anything past the end
// of the recording.
const TS_RE = /([\[(])?(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?:(\s*[-–—]\s*)(\d{1,2}):([0-5]\d)(?::([0-5]\d))?)?([\])])?/g;

function tsSeconds(a, b, c) {
  return c !== undefined ? Number(a) * 3600 + Number(b) * 60 + Number(c) : Number(a) * 60 + Number(b);
}

function linkifyTimestamps(root, maxSec, onSeek) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (/\d:[0-5]\d/.test(n.nodeValue) && !n.parentElement.closest('button, a')) nodes.push(n);
  }
  const fits = (sec) => !maxSec || sec <= maxSec + 2;
  const makeBtn = (label, sec) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ts-link';
    b.textContent = label;
    b.title = t('tipSeekTimestamp');
    b.dataset.ts = String(sec);
    b.addEventListener('click', (e) => { e.preventDefault(); onSeek(sec); });
    return b;
  };
  for (const node of nodes) {
    const text = node.nodeValue;
    const frag = document.createDocumentFragment();
    let last = 0;
    let changed = false;
    TS_RE.lastIndex = 0;
    let m;
    while ((m = TS_RE.exec(text))) {
      const before = text[m.index - 1] || '';
      const after = text[m.index + m[0].length] || '';
      if (/[\d:.]/.test(before) || /\d/.test(after)) continue;  // part of a longer number or a date
      const range = m[6] !== undefined;
      const bracketed = !!(m[1] && m[9]);
      const atLineStart = !text.slice(0, m.index).trim() && !node.previousSibling;
      if (!(bracketed || range || atLineStart || m[4] !== undefined)) continue;
      const s1 = tsSeconds(m[2], m[3], m[4]);
      if (!fits(s1)) continue;
      const label1 = `${m[2]}:${m[3]}${m[4] !== undefined ? ':' + m[4] : ''}`;
      frag.append(text.slice(last, m.index), m[1] || '', makeBtn(label1, s1));
      if (range) {
        const label2 = `${m[6]}:${m[7]}${m[8] !== undefined ? ':' + m[8] : ''}`;
        const s2 = tsSeconds(m[6], m[7], m[8]);
        frag.append(m[5], fits(s2) ? makeBtn(label2, s2) : label2);
      }
      frag.append(m[9] || '');
      last = m.index + m[0].length;
      changed = true;
    }
    if (!changed) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
}

// The model for this request: "cloud" for the cloud model, otherwise an Ollama tag
function summaryModelChoice() {
  const value = document.getElementById('summary-llm-select')?.value || '';
  if (value === '__cloud') {
    const c = cloudLlmChoice();
    return { chosenModel: 'cloud', prettyModel: c ? c.label : 'Cloud' };
  }
  return { chosenModel: value || undefined, prettyModel: formatModelName(value || 'Ollama') };
}

// "Since last time" (app/series.py): the previous meeting of the series checked against this one
async function generateSinceLast() {
  const m = state.activeMeeting;
  const btn = document.getElementById('generate-summary-btn');
  const box = document.getElementById('summary-content');
  const { chosenModel } = summaryModelChoice();
  btn.disabled = true;
  box.innerHTML = `<div class="py-12 text-center text-xs text-apple-secondary animate-pulse">${escapeHtml(t('sinceLastWorking'))}</div>`;
  try {
    const res = await fetch(`/api/meetings/${m.id}/since-last`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: chosenModel, lang: state.uiLang }),
    });
    const data = await readJson(res);
    if (!res.ok) throw new Error(data.detail || 'Error');
    m.summaries = data.summaries;
    renderSummaryPanel();
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderSeriesUi() {
  const m = state.activeMeeting;
  const tab = document.getElementById('sumtab-since_last');
  if (tab) tab.classList.toggle('hidden', !(m && m.series_id));
  if (state.activeSummaryTab === 'since_last' && !(m && m.series_id)) selectSummaryTab('protocol');
}

async function generateSummaryForActiveTab() {
  if (!state.activeMeeting) return;
  if (state.activeSummaryTab === 'since_last') { await generateSinceLast(); return; }
  if (state.activeSummaryTab === 'commitments') { await extractCommitments(); return; }
  const btn = document.getElementById('generate-summary-btn');
  const box = document.getElementById('summary-content');
  const { chosenModel, prettyModel } = summaryModelChoice();
  btn.disabled = true;
  box.innerHTML = `<div class="py-12 flex items-center justify-center gap-2 text-xs text-apple-secondary animate-pulse"><span>${t('uiGeneratingAnalysis')}</span><span class="px-2 py-0.5 rounded-md bg-white/10 text-white font-medium">${escapeHtml(prettyModel)}</span></div>`;

  const forId = state.activeMeeting.id;  // the answer may come minutes later, for another recording
  try {
    const res = await fetch(`/api/meetings/${forId}/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: state.activeSummaryTab, model: chosenModel, lang: state.uiLang })
    });
    const data = await readJson(res);
    if (!res.ok) throw new Error(data.detail || 'Error');
    if (!state.activeMeeting || state.activeMeeting.id !== forId) return;
    state.activeMeeting.summaries = data.summaries;
    renderChaptersBar();
    renderSummaryPanel();
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

async function runCustomQuestion() {
  if (!state.activeMeeting) return;
  const input = document.getElementById('custom-summary-prompt');
  const prompt = input.value.trim();
  if (!prompt) return;

  const { chosenModel, prettyModel } = summaryModelChoice();
  const box = document.getElementById('summary-content');
  box.innerHTML = `<div class="py-12 flex items-center justify-center gap-2 text-xs text-apple-secondary animate-pulse"><span>${t('uiSearchingForAnswer')}</span><span class="px-2 py-0.5 rounded-md bg-white/10 text-white font-medium">${escapeHtml(prettyModel)}</span></div>`;

  const forId = state.activeMeeting.id;
  try {
    const res = await fetch(`/api/meetings/${forId}/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: 'custom', custom_prompt: prompt, model: chosenModel, lang: state.uiLang })
    });
    const data = await readJson(res);
    if (!res.ok) throw new Error(data.detail || 'Error');
    if (!state.activeMeeting || state.activeMeeting.id !== forId) return;
    state.activeMeeting.summaries = data.summaries;
    renderChaptersBar();
    state.activeSummaryTab = 'custom';
    renderSummaryPanel();
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  }
}

function copySummaryText() {
  if (!state.activeMeeting) return;
  const text = ((state.activeMeeting.summaries || {})[state.activeSummaryTab] || '').replaceAll('[?]', `(${t('unverifiedBadge')})`);
  if (!text) return;
  navigator.clipboard.writeText(text);
  showToastt('uiCopied');
}

// --- Export ---
async function exportMeetingById(id, fmt, opts) {
  fmt = (fmt || 'txt').toLowerCase();
  try {
    const qs = opts && opts.lang ? `?${new URLSearchParams({ lang: opts.lang, variant: opts.variant || 'translated' })}` : '';
    const res = await fetch(`/api/meetings/${id}/export/${fmt}${qs}`);
    if (!res.ok) {
      showToast(t('exportFailed'));
      return;
    }
    const blob = await res.blob();

    // Extract filename from Content-Disposition header with fallback
    const disp = res.headers.get('content-disposition') || '';
    let filename = '';
    const matchUtf8 = disp.match(/filename\*=UTF-8''([^;]+)/i);
    const matchPlain = disp.match(/filename="?([^";]+)"?/i);
    if (matchUtf8 && matchUtf8[1]) {
      try { filename = decodeURIComponent(matchUtf8[1]); } catch (e) { filename = matchUtf8[1]; }
    } else if (matchPlain && matchPlain[1]) {
      filename = matchPlain[1];
    }
    if (!filename) {
      const m = state.meetings.find(x => x.id === id) || state.activeMeeting;
      const title = (m && m.title) || 'meeting';
      const safeTitle = title.replace(/[^a-zA-Z0-9а-яА-ЯёЁ _-]/g, '_').trim() || 'meeting';
      filename = `${safeTitle}.${fmt}`;
    }

    // Method 1: File System Access API (Native Windows "Save As" file dialog in WebView2 / Chromium)
    if (typeof window.showSaveFilePicker === 'function') {
      const typeMap = {
        docx: { description: 'Word Document (*.docx)', accept: { 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'] } },
        md:   { description: 'Markdown (*.md)', accept: { 'text/markdown': ['.md'] } },
        txt:  { description: 'Text File (*.txt)', accept: { 'text/plain': ['.txt'] } },
        srt:  { description: 'Subtitles (*.srt)', accept: { 'text/plain': ['.srt'] } },
        json: { description: 'JSON File (*.json)', accept: { 'application/json': ['.json'] } },
      };
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: filename,
          types: typeMap[fmt] ? [typeMap[fmt]] : undefined,
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        showToast(`${t('exportSavedTo')} ${handle.name}`);
        return;
      } catch (err) {
        // User cancelled the file picker dialog
        if (err && (err.name === 'AbortError' || err.code === 20)) {
          return;
        }
        console.warn('showSaveFilePicker fallback:', err);
      }
    }

    // Method 2: Native download trigger via Blob URL
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    }, 4000);
    showToast(`${t('exportSavedTo')} ${filename}`);
  } catch (e) {
    console.error('Export error:', e);
    showToast(t('exportFailed'));
  }
}

async function revealPath(path) {
  try {
    await fetch('/api/reveal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
  } catch (e) {}
}

async function refreshExportDirLabel() {
  const el = document.getElementById('set-export-dir');
  if (!el) return;
  try {
    const res = await fetch('/api/export-dir');
    const data = await readJson(res);
    el.textContent = data.folder || '';
    el.title = data.folder || '';
  } catch (e) {}
}

async function pickExportDir() {
  try {
    const res = await fetch('/api/export-dir/pick', { method: 'POST' });
    const data = await readJson(res);
    if (data && data.ok) refreshExportDirLabel();
  } catch (e) {}
}

// --- Folder for recordings made in the app ---
async function refreshRecordingsDirLabel() {
  try {
    const data = await (await fetch('/api/recordings-dir')).json();
    const el = document.getElementById('set-recordings-dir');
    el.textContent = data.folder || '';
    el.title = data.folder || '';
    document.getElementById('recdir-reset-btn').classList.toggle('hidden', !data.folder || data.folder === data.default);
    const uploads = state.system && state.system.paths && state.system.paths.uploads;
    document.getElementById('set-imports-dir').textContent = uploads ? t('recDirImports').replace('{dir}', () => uploads) : '';
  } catch (e) {}
}

async function pickRecordingsDir() {
  const res = await fetch('/api/recordings-dir/pick', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  if (data.ok) await afterRecordingsDirChange();
}

async function resetRecordingsDir() {
  await fetch('/api/recordings-dir/reset', { method: 'POST' });
  await afterRecordingsDirChange();
}

async function afterRecordingsDirChange() {
  await loadSystemInfo();
  refreshRecordingsDirLabel();
  renderRecordSaveHint();
  showToast(t('recDirChanged'));
}

function openRecordingsDir() {
  const el = document.getElementById('set-recordings-dir');
  if (el && el.textContent) revealPath(el.textContent);
}

// --- Obsidian settings ---
const OBSIDIAN_TOGGLES = ['auto', 'generate', 'summary', 'tasks', 'chapters', 'transcript'];

function fillObsidianSettings(s) {
  for (const k of OBSIDIAN_TOGGLES) {
    document.getElementById(`set-obsidian-${k}`).checked = (s[`obsidian_${k}`] || (k === 'auto' ? 'false' : 'true')) === 'true';
  }
  document.getElementById('set-obsidian-subfolder').value = s.obsidian_subfolder ?? 'VerbaScribe';
  document.getElementById('set-obsidian-filename').value = s.obsidian_filename || '{date} {title}';
  document.getElementById('set-file-name-template').value = s.file_name_template || '{date} {title}';
  state.fileNameTemplate = s.file_name_template || '{date} {title}';
  if (libPrefs().names === 'file' && state.meetings) renderMeetingsSidebar();
  document.getElementById('set-date-folders').value = s.date_folders || 'off';
  updateFileNamePreview();
  const vault = document.getElementById('set-obsidian-vault');
  vault.textContent = s.obsidian_vault || t('obsVaultNone');
  vault.title = s.obsidian_vault || '';
  vault.dataset.path = s.obsidian_vault || '';
  updateObsidianPreview();
}

function obsidianSettingsPayload() {
  const out = {
    obsidian_subfolder: document.getElementById('set-obsidian-subfolder').value.trim(),
    obsidian_filename: document.getElementById('set-obsidian-filename').value.trim() || '{date} {title}',
    file_name_template: document.getElementById('set-file-name-template').value.trim() || '{date} {title}',
    date_folders: document.getElementById('set-date-folders').value || 'off',
  };
  for (const k of OBSIDIAN_TOGGLES) out[`obsidian_${k}`] = String(document.getElementById(`set-obsidian-${k}`).checked);
  return out;
}

function meetingDate(m) {
  const d = m ? new Date(String(m.recorded_at || m.created_at || '').replace(' ', 'T')) : new Date();
  return isNaN(d) ? new Date() : d;
}

// A recording's name by the file name template (as its files are named), without the extension
function formatFileName(m, tpl) {
  const dt = meetingDate(m);
  const p2 = n => String(n).padStart(2, '0');
  const ymd = `${dt.getFullYear()}-${p2(dt.getMonth() + 1)}-${p2(dt.getDate())}`;
  return (tpl || state.fileNameTemplate || '{date} {title}')
    .replaceAll('{date}', ymd).replaceAll('{time}', `${p2(dt.getHours())}-${p2(dt.getMinutes())}`)
    .replaceAll('{title}', (m && m.title) || t('fileNameExampleTitle')).replaceAll('{lang}', ((m && m.language) || 'ru').toUpperCase())
    .replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, ' ').trim();
}

// Settings -> Export: tokens to insert and an example of the folder and file name
const FILE_NAME_TOKENS = ['{date}', '{time}', '{title}', '{lang}'];

function updateFileNamePreview() {
  const input = document.getElementById('set-file-name-template');
  const tokens = document.getElementById('file-name-tokens');
  if (tokens && !tokens.childElementCount) {
    tokens.innerHTML = FILE_NAME_TOKENS.map(tk => `<button type="button" data-tk="${tk}" class="ui-btn ui-btn-subtle h-6 !px-2 text-[10.5px] font-mono">${tk}</button>`).join('');
    tokens.onclick = e => {
      const b = e.target.closest('[data-tk]');
      if (!b) return;
      const pos = input.selectionStart ?? input.value.length;
      input.value = input.value.slice(0, pos) + b.dataset.tk + input.value.slice(input.selectionEnd ?? pos);
      input.focus();
      updateFileNamePreview();
    };
  }
  const m = state.activeMeeting;
  const name = formatFileName(m, input.value.trim());
  const dt = meetingDate(m);
  const p2 = n => String(n).padStart(2, '0');
  const ymd = `${dt.getFullYear()}-${p2(dt.getMonth() + 1)}-${p2(dt.getDate())}`;
  const mode = document.getElementById('set-date-folders').value;
  const folder = { off: '', day: ymd, month: ymd.slice(0, 7), year_month: `${ymd.slice(0, 4)}\\${ymd.slice(0, 7)}` }[mode] || '';
  document.getElementById('file-name-preview').textContent = `${t('fileNameExample')}: ${folder ? folder + '\\' : ''}${name}.md`;
}

function updateObsidianPreview() {
  const vault = document.getElementById('set-obsidian-vault').dataset.path || '';
  const sub = document.getElementById('set-obsidian-subfolder').value.trim();
  const tpl = document.getElementById('set-obsidian-filename').value.trim() || '{date} {title}';
  const m = state.activeMeeting;
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const name = tpl.replace('{date}', () => date).replace('{time}', () => `${String(d.getHours()).padStart(2, '0')}-${String(d.getMinutes()).padStart(2, '0')}`)
    .replace('{title}', () => (m && m.title) || t('obsExampleTitle')).replace('{lang}', () => ((m && m.language) || 'ru').toUpperCase());
  const sep = vault.includes('/') && !vault.includes('\\') ? '/' : '\\';
  document.getElementById('obs-preview').textContent = vault
    ? `${t('obsPreview')}: ${[vault, sub, name + '.md'].filter(Boolean).join(sep)}`
    : t('obsPreviewNoVault');
}

async function refreshObsidianStatus() {
  try {
    const st = await (await fetch('/api/obsidian/status')).json();
    const pill = document.getElementById('obs-pill');
    const [text, cls] = !st.vault ? [t('obsPillNone'), 'bg-white/5 text-apple-secondary']
      : !st.exists ? [t('obsPillMissing'), 'bg-apple-red/15 text-apple-red']
      : st.is_vault ? [t('obsPillVault'), 'bg-apple-green/15 text-apple-green']
      : [t('obsPillFolder'), 'bg-apple-amber/15 text-apple-amber'];
    pill.textContent = text;
    pill.className = `text-[10px] font-mono px-2.5 py-0.5 rounded-full shrink-0 ${cls}`;
    pill.title = st.is_vault || !st.vault ? '' : t('tipObsNotVault');
  } catch (e) {}
}

async function pickObsidianVault() {
  const res = await fetch('/api/obsidian/pick-vault', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) return;
  if (state.system && state.system.settings) state.system.settings.obsidian_vault = data.folder;
  const vault = document.getElementById('set-obsidian-vault');
  vault.textContent = data.folder;
  vault.title = data.folder;
  vault.dataset.path = data.folder;
  updateObsidianPreview();
  refreshObsidianStatus();
  if (!data.is_vault) showToast(t('tipObsNotVault'));
}

async function openObsidianFolder() {
  try {
    const st = await (await fetch('/api/obsidian/status')).json();
    if (st.folder || st.vault) revealPath(st.exists ? st.vault : '');
  } catch (e) {}
}

async function unloadAllModelsNow() {
  const res = await fetch('/api/vram/unload-all', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  renderVram(data);
  showToast((data.released || []).length ? t('idleUnloadedNow') : t('idleNothingLoaded'));
}

function openExportDir() {
  const el = document.getElementById('set-export-dir');
  if (el && el.textContent) revealPath(el.textContent);
}

// The language model the first-run wizard picks, downloaded from its card
function pullFirstRunLlm() {
  pullOllamaModel(firstRunPicks(state.system || {}, state.uiLang).llm, true);
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// A value as JSON for the arguments of an event (core.js PAGE_EVENTS): data-click="f" data-click-args='[${jsArg(name)}]'.
// Any text is safe there (quotes included); a value never goes in without it, or Number() for numbers.
function jsArg(value) {
  return escapeHtml(JSON.stringify(value ?? null));
}

// --- "Details" tab: where the file is, what it is, and what was done with it ---
async function loadMeetingInfo() {
  const box = document.getElementById('info-content');
  const m = state.activeMeeting;
  if (!m) return;
  const id = m.id;
  box.innerHTML = `<div class="py-10 text-center text-xs text-apple-secondary animate-pulse">…</div>`;
  try {
    const res = await fetch(`/api/meetings/${id}/info`);
    const info = await readJson(res);
    if (!res.ok) throw new Error(info.detail || 'Error');
    if (!state.activeMeeting || state.activeMeeting.id !== id) return;
    renderMeetingInfo(info);
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  }
}

function formatDateTime(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return s ? String(s) : '—';
  try {
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]))
      .toLocaleString(state.uiLang === 'en' ? 'en-GB' : 'ru-RU', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch (e) {
    return String(s);
  }
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => showToast(t('copied')), () => {});
}

function renderMeetingInfo(info) {
  const box = document.getElementById('info-content');
  const row = (label, value, opts = {}) => value == null || value === '' ? '' : `
    <div class="flex items-start gap-4 px-4 py-2.5 text-xs">
      <div class="w-40 shrink-0 text-apple-secondary">${escapeHtml(label)}</div>
      <div class="flex-1 min-w-0 text-apple-label ${opts.mono ? 'font-mono text-[11.5px]' : ''} break-all select-text">${opts.html ? value : escapeHtml(String(value))}</div>
    </div>`;
  const card = (title, rows, actions = '') => rows.trim() ? `
    <div class="rounded-xl border apple-border bg-apple-sidebar overflow-hidden">
      <div class="px-4 py-2.5 border-b apple-border flex items-center justify-between gap-2">
        <h4 class="text-xs font-semibold text-white">${escapeHtml(title)}</h4>
        <div class="flex items-center gap-1.5">${actions}</div>
      </div>
      <div class="divide-y divide-white/5">${rows}</div>
    </div>` : '';
  const pathActions = (p) => p ? `
    <button data-click="revealPath" data-click-args='[{"$": "data", "k": "p"}]' data-p="${escapeHtml(p)}" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('showInFolder'))}</button>
    <button data-click="copyText" data-click-args='[{"$": "data", "k": "p"}]' data-p="${escapeHtml(p)}" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('copyPath'))}</button>` : '';

  const media = info.media || {};
  const probe = info.probe || {};
  const v = probe.video;
  const a = probe.audio;
  const sourceName = t(`infoSource_${info.source || 'import'}`);
  const folderPath = (info.folders || []).map(f => f.system_key ? t(`folderSystem_${f.system_key}`) : f.name).join(' / ');

  let html = card(t('infoFile'),
    row(t('infoName'), media.name || info.filename)
    + row(t('infoLocation'), media.path, { mono: true })
    + (media.exists === false ? row(t('infoState'), `<span class="text-apple-red">${escapeHtml(t('infoMissing'))}</span>`, { html: true }) : '')
    + row(t('infoSize'), media.size != null ? formatBytes(media.size) : '')
    + row(t('infoSourceLabel'), sourceName + (info.owns_media ? '' : ` · ${t('infoInPlace')}`))
    + row(t('infoFolder'), folderPath || t('infoNoFolder')),
    pathActions(media.exists ? media.path : ''));

  html += card(t('infoDates'),
    row(t('infoRecordedAt'), formatDateTime(info.recorded_at))
    + row(t('infoAddedAt'), formatDateTime(info.created_at))
    + row(t('infoFileModified'), media.modified ? formatDateTime(media.modified) : '')
    + row(t('infoProcessedAt'), info.completed_at ? formatDateTime(info.completed_at) : ''));

  html += card(t('infoFormat'),
    row(t('infoContainer'), probe.container)
    + row(t('infoDuration'), formatTime(probe.duration || info.duration))
    + (v ? row(t('infoVideo'), [v.codec && v.codec.toUpperCase(), v.width && v.height ? `${v.width}×${v.height}` : '', v.fps ? `${v.fps} fps` : ''].filter(Boolean).join(' · ')) : '')
    + (a ? row(t('infoAudio'), [a.codec && a.codec.toUpperCase(), a.sample_rate ? `${(a.sample_rate / 1000).toFixed(1)} kHz` : '', a.layout || (a.channels ? `${a.channels} ch` : ''), a.bitrate_kbps ? `${a.bitrate_kbps} kbps` : ''].filter(Boolean).join(' · ')) : '')
    + row(t('infoBitrate'), probe.bitrate_kbps ? `${probe.bitrate_kbps} kbps` : ''));

  html += card(t('infoProcessing'),
    row(t('infoStatus'), tMaybe(`status_${info.status}`) || info.status)
    + row(t('infoModel'), formatModelName(info.model_name))
    + row(t('infoLanguage'), !info.language ? '' : info.language === 'auto' ? t('studioSpeechAuto')
      : `${langName(info.language)} (${String(info.language).toUpperCase()})`)
    + row(t('infoCounts'), t('infoCountsValue').replace('{spk}', () => info.speakers).replace('{seg}', () => info.segments).replace('{words}', () => info.words))
    + row(t('infoAnalyses'), (info.summaries || []).filter(k => k !== 'error')
      .map(k => tMaybe(`summaryName_${k}`) || ((state.summaryTemplates || []).find(x => x.id === k) || {}).name || k).join(', '))
    + row(t('infoTranslations'), (info.translations || []).map(c => langName(c)).join(', '))
    + row(t('infoWorkAudio'), info.audio && info.audio.exists ? `${info.audio.path} · ${formatBytes(info.audio.size)}` : '', { mono: true }));

  const dubs = (info.dubs || []).filter(Boolean);
  if (dubs.length || info.obsidian) {
    html += card(t('infoOutputs'),
      dubs.map(d => row(t('infoDubFile'), `${d.path}${d.size != null ? ' · ' + formatBytes(d.size) : ''}`, { mono: true })).join('')
      + (info.obsidian ? row('Obsidian', info.obsidian.path + (info.obsidian.exists ? '' : ` (${t('infoMissing')})`), { mono: true }) : ''),
      info.obsidian && info.obsidian.exists ? pathActions(info.obsidian.path) : '');
  }
  if ((info.pending_exports || []).length) {
    html += `<div class="rounded-xl border border-apple-amber/40 bg-apple-amber/10 px-4 py-3 text-xs text-apple-amber flex items-center justify-between gap-3">
      <span>${escapeHtml(t('infoPendingExports').replace('{n}', () => info.pending_exports.length))}</span>
      <button data-click="cancelPendingExports" data-click-args='[${info.id}]' class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('cancel'))}</button>
    </div>`;
  }
  box.innerHTML = html;
}

async function cancelPendingExports(id) {
  await fetch(`/api/meetings/${id}/pending-exports`, { method: 'DELETE' });
  loadMeetingInfo();
}

// --- Text context menu ---
// The app window has no browser menu of its own, so right-click on text opens this one: in a text
// field it edits (cut, copy, paste, select all); on selected text it copies the text and offers what
// the app can do with it (play from there, search, ask the assistant, add to the dictionary).
const TEXT_AREAS = '#studio-captions, #transcript-container, #summary-content, #ask-thread, #info-content, .markdown-body';

function closeTextMenu() {
  document.getElementById('text-menu')?.remove();
}

function textMenuItems(e) {
  const el = e.target;
  if (isTypingTarget(el) && el.tagName !== 'SELECT') {
    const field = el;
    const ro = field.readOnly || field.disabled;
    const sel = field.isContentEditable ? String(window.getSelection()) : field.value.slice(field.selectionStart ?? 0, field.selectionEnd ?? 0);
    return [
      { label: t('ctxCut'), key: 'Ctrl+X', disabled: ro || !sel, run: () => { field.focus(); document.execCommand('cut'); } },
      { label: t('ctxCopy'), key: 'Ctrl+C', disabled: !sel, run: () => copyPlain(sel) },
      { label: t('ctxPaste'), key: 'Ctrl+V', disabled: ro, run: () => pasteInto(field) },
      { sep: true },
      { label: t('ctxSelectAll'), key: 'Ctrl+A', run: () => { field.focus(); field.select ? field.select() : document.execCommand('selectAll'); } },
    ];
  }
  const area = el.closest && el.closest(TEXT_AREAS);
  const selection = window.getSelection();
  const text = String(selection || '').replace(/\s+/g, ' ').trim();
  const inSel = text && selection.rangeCount && area && area.contains(selection.getRangeAt(0).commonAncestorContainer);
  if (!area && !text) return null;
  const items = [];
  const segRow = (inSel ? nodeEl(selection.anchorNode) : el)?.closest?.('.segment-row');
  const inStudio = area && area.id === 'studio-captions';
  if (text && (inSel || !area)) {
    items.push({ label: t('ctxCopy'), key: 'Ctrl+C', run: () => copyPlain(String(selection)) });
    if (segRow) {
      const start = selectionStartTime(selection) ?? Number(segRow.dataset.start);
      items.push({ label: t('ctxCopyWithTime'), run: () => copyPlain(`[${formatTime(start)}] ${text}`) });
      items.push({ sep: true });
      items.push({ label: t('ctxPlayFromHere'), key: formatTime(start), run: () => seekPlayer(start) });
    }
    items.push({ sep: true });
    const short = text.length <= 80;
    if (state.activeMeeting && short && document.getElementById('library-view') && !document.getElementById('library-view').classList.contains('hidden')) {
      items.push({ label: t('ctxFindHere'), run: () => findInTranscript(text) });
    }
    if (short) items.push({ label: t('ctxFindAll'), run: () => findEverywhere(text) });
    items.push({ label: t('ctxAskAssistant'), run: () => askAbout(text) });
    if (text.length <= 60 && text.split(' ').length <= 5) {
      items.push({ label: t('ctxAddToDictionary'), run: () => addToDictionary(text) });
    }
  } else if (segRow) {
    // A word under the cursor: it can go to the dictionary as it is
    const wordEl = el.closest && el.closest('.word-token');
    const word = wordEl ? wordEl.textContent.replace(/^[\s.,!?;:«»"'()]+|[\s.,!?;:«»"'()]+$/g, '') : '';
    if (word) {
      items.push({ label: t('ctxAddWordToDictionary').replace('{word}', () => word), run: () => addToDictionary(word) });
      items.push({ label: t('ctxPlayFromHere'), key: formatTime(Number(wordEl.dataset.start)), run: () => seekPlayer(Number(wordEl.dataset.start)) });
      items.push({ sep: true });
    }
    const segText = segRow.querySelector('[id^="seg-text-"]')?.innerText.replace(/\s+/g, ' ').trim() || '';
    const start = Number(segRow.dataset.start);
    items.push({ label: t('ctxCopyLine'), run: () => copyPlain(segText) });
    items.push({ label: t('ctxCopyWithTime'), run: () => copyPlain(`[${formatTime(start)}] ${segText}`) });
    items.push({ label: t('ctxPlayFromHere'), key: formatTime(start), run: () => seekPlayer(start) });
  } else if (inStudio) {
    const row = el.closest('.cap-row');
    const line = row ? capRowText(row) : '';
    if (line) items.push({ label: t('ctxCopyLine'), run: () => copyPlain(line) });
  }
  if (area) {
    items.push({ sep: true });
    if (inStudio) items.push({ label: t('ctxCopyAllCaptions'), disabled: !capView.rows.size, run: () => copyPlain(studioCaptionsText()) });
    items.push({ label: t('ctxSelectAll'), key: 'Ctrl+A', run: () => selectAllIn(inStudio ? document.getElementById('studio-cap-rows') : area) });
  }
  return items;
}

function nodeEl(node) {
  return node && (node.nodeType === 1 ? node : node.parentElement);
}

// The time of the first selected word, when the words carry their timings
function selectionStartTime(selection) {
  const range = selection.getRangeAt(0);
  const startEl = nodeEl(range.startContainer);
  const word = startEl?.closest('.word-token');
  if (word) return Number(word.dataset.start);
  const row = startEl?.closest('.segment-row');
  const first = row && [...row.querySelectorAll('.word-token')].find(w => range.intersectsNode(w));
  return first ? Number(first.dataset.start) : null;
}

function capRowText(row) {
  const o = row.querySelector('.cap-orig')?.textContent.trim() || '';
  const tr = row.querySelector('.cap-tr')?.textContent.trim() || '';
  return tr && tr !== o ? `${o}\n${tr}` : o;
}

function studioCaptionsText() {
  return [...capView.rows.values()].filter(r => r.key !== capView.draftKey).map(r => capRowText(r.el)).filter(Boolean).join('\n\n');
}

function selectAllIn(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function copyPlain(text) {
  if (text) copyText(text);
}

async function pasteInto(field) {
  field.focus();
  try {
    const text = await navigator.clipboard.readText();
    if (field.isContentEditable) {
      document.execCommand('insertText', false, text);
    } else {
      field.setRangeText(text, field.selectionStart, field.selectionEnd, 'end');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  } catch (e) {
    showToast(t('ctxPasteBlocked'));
  }
}

function findInTranscript(text) {
  focusTranscriptSearch();
  const input = document.getElementById('transcript-search');
  input.value = text;
  renderTranscript();
}

function findEverywhere(text) {
  switchAppView('library');
  const input = document.getElementById('global-search-input');
  input.value = text;
  input.focus();
  handleGlobalSidebarSearch(text);
}

function askAbout(text) {
  const inLibrary = !document.getElementById('library-view')?.classList.contains('hidden');
  const scope = inLibrary && state.activeMeeting ? { type: 'meeting', id: state.activeMeeting.id } : null;
  openLibraryAsk(t('ctxAskTemplate').replace('{text}', () => text.length > 300 ? text.slice(0, 300) + '…' : text), scope);
}

async function addToDictionary(text) {
  const term = text.replace(/^[\s.,!?;:«»"'()]+|[\s.,!?;:«»"'()]+$/g, '');
  if (!term) return;
  const res = await fetch('/api/dictionary', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ term, category: 'Термин' }),
  });
  if (res.ok) {
    showToast(t('ctxAddedToDictionary').replace('{term}', () => term));
    loadDictionary();
  }
}

document.addEventListener('contextmenu', (e) => {
  closeTextMenu();
  if (e.defaultPrevented) return;
  const items = textMenuItems(e);
  if (!items || !items.some(i => !i.sep)) return;
  e.preventDefault();
  openTextMenuAt(items, e.clientX, e.clientY);
});

// A small menu of items ({label, key, run, disabled} or {sep}) at a point of the window
function openTextMenuAt(items, x, y) {
  closeTextMenu();
  closeQuickLangMenu();
  const menu = document.createElement('div');
  menu.id = 'text-menu';
  menu.className = 'fixed min-w-[220px] macos-menu z-[60]';
  const clean = items.filter((it, i, a) => !it.sep || (i > 0 && !a[i - 1].sep && i < a.length - 1));
  menu.innerHTML = clean.map((it, i) => it.sep
    ? '<div class="my-1 h-px bg-white/10"></div>'
    : `<button class="macos-menu-item gap-6" data-i="${i}" ${it.disabled ? 'disabled style="opacity:.4"' : ''}><span>${escapeHtml(it.label)}</span>`
      + `<span class="text-[10.5px] opacity-80 font-mono">${escapeHtml(it.key || '')}</span></button>`).join('');
  // Keeps the selection: a click in the menu must not clear it before the action reads it
  menu.addEventListener('mousedown', ev => ev.preventDefault());
  menu.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-i]');
    ev.stopPropagation();
    if (!b || b.disabled) return;
    closeTextMenu();
    clean[Number(b.dataset.i)].run();
  });
  document.body.appendChild(menu);
  // x, y are a point of the window (clientX/Y); the menu is placed in the page's own pixels
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(zpx(x), viewW() - zpx(r.width) - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(zpx(y), viewH() - zpx(r.height) - 4))}px`;
}
document.addEventListener('click', closeTextMenu);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTextMenu(); });
window.addEventListener('blur', closeTextMenu);
document.addEventListener('scroll', closeTextMenu, true);

// --- About (GET /api/about) and its easter eggs: the willow hears everything ---
const ABOUT_LINES = {
  ru: [
    'Участник 3 просил не записывать. Записано.',
    '[неразборчиво] — но мы поняли.',
    'Ваша последняя фраза уже в протоколе. [00:00]',
    'Пункт помечен [?]: доказательств нет. Пока.',
    'Слова-паразиты удалены. Паразиты — нет.',
    'Обещание найдено: «сделаю к пятнице». Пятница не найдена.',
    'Верба не спит. Верба транскрибирует.',
    'Тишина длилась 3 секунды. Расшифровано: «…».',
    'Голос узнан: это вы. Опять.',
  ],
  en: [
    'Participant 3 asked not to be recorded. Recorded.',
    '[inaudible], but we got it.',
    'Your last sentence is already in the minutes. [00:00]',
    'Point marked [?]: no evidence. Yet.',
    'Filler words removed. The fillers themselves are still here.',
    'Promise found: "done by Friday". Friday not found.',
    'The willow does not sleep. The willow transcribes.',
    'Silence lasted 3 seconds. Transcribed: "…".',
    'Voice recognized: it is you. Again.',
  ],
};
const ABOUT_EGG = {
  ru: [
    ['00:00', 'Участник 1', '#64D2FF', '…он опять кликает по вербе.'],
    ['00:02', 'Участник 2', '#BF5AF2', 'Пусть. Мы всё равно всё записываем.'],
    ['00:04', 'Участник ?', '#FF453A', 'Тише. Он читает субтитры.'],
    ['00:07', null, null, 'Разделение по голосам: найдено 4 участника. В комнате — 1 человек. [?]'],
    ['00:09', null, null, 'Обещание: «больше не кликать по логотипу». Срок: никогда.'],
    ['00:11', 'Участник 4', '#FF9F0A', 'Не оборачивайся.'],
  ],
  en: [
    ['00:00', 'Participant 1', '#64D2FF', '…he is clicking the willow again.'],
    ['00:02', 'Participant 2', '#BF5AF2', 'Let him. We are recording everything anyway.'],
    ['00:04', 'Participant ?', '#FF453A', 'Quiet. He is reading the captions.'],
    ['00:07', null, null, 'Speaker separation: 4 participants found. People in the room: 1. [?]'],
    ['00:09', null, null, 'Promise: "no more clicking the logo". Due: never.'],
    ['00:11', 'Participant 4', '#FF9F0A', 'Do not turn around.'],
  ],
};
const about = { clicks: [], line: -1, typing: null, egg: [], eggOn: false };

async function openAbout() {
  hideContextMenu();
  const d = await fetch('/api/about').then(r => r.json()).catch(() => null);
  const en = state.uiLang === 'en';
  if (d) {
    document.getElementById('about-version').textContent = d.version;
    const since = d.since ? d.since.split('-').reverse().join('.') : '';
    const n = d.recordings;
    const amount = d.hours >= 1 ? `${d.hours} ${en ? 'h' : 'ч'}` : `${Math.max(1, Math.round(d.hours * 60))} ${en ? 'min' : 'мин'}`;
    const recs = en ? (n === 1 ? 'recording' : 'recordings') : (n % 10 === 1 && n % 100 !== 11 ? 'записи' : 'записях');
    document.getElementById('about-stats').textContent = n
      ? (en ? `Transcribed ${amount} of speech in ${n} ${recs} since ${since}` : `Расшифровано ${amount} речи в ${n} ${recs} с ${since}`)
      : (en ? 'Nothing transcribed yet: the willow is waiting' : 'Пока ничего не расшифровано — верба ждёт');
  }
  document.getElementById('about-caption').textContent = '';
  endAboutEgg();
  openModal('about');
}

function aboutTypeLine(text) {
  const box = document.getElementById('about-caption');
  clearInterval(about.typing);
  let i = 0;
  box.innerHTML = '<span></span><span class="caret"></span>';
  const span = box.firstChild;
  about.typing = setInterval(() => {
    span.textContent = text.slice(0, ++i);
    if (i >= text.length) clearInterval(about.typing);
  }, 28);
}

function aboutLogoClick() {
  if (about.eggOn) return;
  const logo = document.getElementById('about-logo');
  logo.classList.remove('about-shake');
  void logo.offsetWidth;  // restart the animation
  logo.classList.add('about-shake');
  releaseWillowFluff(10, 1.3, logo);
  const now = Date.now();
  about.clicks = about.clicks.filter(t => now - t < 2000).concat(now);
  if (about.clicks.length >= 5) { about.clicks = []; startAboutEgg(); return; }
  const lines = ABOUT_LINES[state.uiLang === 'en' ? 'en' : 'ru'];
  about.line = (about.line + 1) % lines.length;
  aboutTypeLine(lines[about.line]);
}

function startAboutEgg() {
  const en = state.uiLang === 'en';
  const box = document.getElementById('about-egg');
  const list = document.getElementById('about-egg-lines');
  const timer = document.getElementById('about-egg-timer');
  list.innerHTML = '';
  box.classList.remove('hidden');
  box.parentElement.classList.add('about-egg-on');  // room for the whole transcript
  about.eggOn = true;
  const started = Date.now();
  const tick = setInterval(() => {
    const s = Math.floor((Date.now() - started) / 1000);
    timer.textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }, 250);
  about.egg.push(tick);
  // Now and then the whole window glitches, every 2.5-5 s (its last line glitches too)
  const glitch = () => {
    box.classList.remove('egg-glitch');
    void box.offsetWidth;  // restart the animation
    box.classList.add('egg-glitch');
    about.egg.push(setTimeout(glitch, 2500 + Math.random() * 2500));
  };
  about.egg.push(setTimeout(glitch, 1800));
  const lines = ABOUT_EGG[en ? 'en' : 'ru'];
  lines.forEach(([ts, who, color, text], i) => {
    about.egg.push(setTimeout(() => {
      const row = document.createElement('div');
      row.innerHTML = `<span class="ts">[${ts}]</span>` + (who
        ? `<span class="who" style="color:${color}">${escapeHtml(who)}:</span><span class="text-white">${escapeHtml(text)}</span>`
        : `<span class="sys">${escapeHtml(text)}</span>`);
      list.appendChild(row);
    }, 900 + i * 1700));
  });
  about.egg.push(setTimeout(() => {
    const row = document.createElement('div');
    row.className = 'glitch pt-3';
    row.textContent = en ? 'Kidding. Everything runs locally, the microphone is off. Probably.'
      : 'Шутка. Всё работает локально, микрофон выключен. Наверное.';
    list.appendChild(row);
    releaseWillowFluff(60, 3.4, document.getElementById('about-logo'));
  }, 900 + lines.length * 1700 + 600));
  about.egg.push(setTimeout(endAboutEgg, 900 + lines.length * 1700 + 6000));
}

function endAboutEgg() {
  about.egg.forEach(id => { clearTimeout(id); clearInterval(id); });
  about.egg = [];
  about.eggOn = false;
  const box = document.getElementById('about-egg');
  box?.classList.add('hidden');
  box?.classList.remove('egg-glitch');
  box?.parentElement.classList.remove('about-egg-on');
}
