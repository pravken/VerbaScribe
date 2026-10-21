// VerbaScribe page script, part 5 of 9: The open recording: audio tracks, dubbing, translation, the transcript.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// --- Active Meeting Workspace ---
let _selectSeq = 0;

async function selectMeeting(id, reloadPlayer = true) {
  // Only the last request wins: a slow answer for an earlier click must not replace the open recording
  const seq = ++_selectSeq;
  const res = await fetch(`/api/meetings/${id}`);
  if (!res.ok || seq !== _selectSeq) return;
  const m = await readJson(res);
  const prevId = state.activeMeeting ? state.activeMeeting.id : null;
  state.activeMeeting = m;
  if (prevId !== m.id) state.speakerFilter = null;

  document.getElementById('workspace-empty').classList.add('hidden');
  document.getElementById('workspace-content').classList.remove('hidden');

  document.getElementById('meeting-title-input').value = m.title;
  document.getElementById('meeting-meta-badge').textContent =
    `${formatTime(m.duration)} · ${(m.language || 'ru').toUpperCase()} · ${formatModelName(m.model_name)}`;
  document.getElementById('meeting-meta-badge').title = document.getElementById('meeting-meta-badge').textContent;

  updateProcessingBanner(m);
  renderAudioTrackSwitch();
  renderFillerButton();
  renderSeriesUi();
  if (prevId !== m.id || state.activeWorkspaceTab === 'summary') renderAnalysisEstimate();

  if (reloadPlayer || prevId !== m.id) {
    const player = document.getElementById('media-player');
    state.audioTrack = 'orig';
    player.src = `/api/meetings/${m.id}/media`;
    player.volume = state.muted ? 0 : (state.volume / 100);
    document.getElementById('player-total-time').textContent = formatTime(m.duration);
    document.getElementById('player-current-time').textContent = '00:00';
    buildPlayerWaveformEnvelope(m);
  }

  renderSpeakerPillsBar();
  renderSpeakersPanel();
  renderTranslationControls();
  renderChaptersBar();
  renderTranscript();
  renderSummaryPanel();
  renderMeetingsSidebar();
  if (state.activeWorkspaceTab === 'info' && (prevId !== m.id || reloadPlayer)) loadMeetingInfo();
  if (state.appView === 'ask') renderAskScope();
}

function updateProcessingBanner(m) {
  const isBusy = !DONE_STATUSES.includes(m.status);
  const banner = document.getElementById('workspace-processing-banner');
  banner.classList.toggle('hidden', !isBusy && m.status !== 'failed' && m.status !== 'cancelled');
  document.getElementById('workspace-cancel-btn').classList.toggle('hidden', !isBusy);
  document.getElementById('workspace-retry-btn').classList.toggle('hidden', isBusy);
  const bar = document.getElementById('workspace-progress-bar');
  const q = (state.queue || []).find(x => x.id === m.id);
  const running = isBusy && q && q.state === 'running';
  bar.classList.toggle('progress-live', !!running);
  document.getElementById('workspace-live-dot').classList.toggle('hidden', !running);
  document.getElementById('workspace-progress-time').textContent = running ? formatTiming(jobTiming(q, m.progress || 0)) : '';
  if (isBusy) {
    document.getElementById('workspace-stage-text').textContent = m.stage_text || t('processingShort');
    document.getElementById('workspace-progress-pct').textContent = `${m.progress}%`;
    bar.style.background = '';
    bar.style.width = `${Math.max(running ? 3 : 0, m.progress || 0)}%`;
  } else {
    // Failed or cancelled: the reason, a red (or grey) bar and Retry, not a "finished" blue bar
    const failed = m.status === 'failed';
    document.getElementById('workspace-stage-text').textContent =
      (failed ? t('statusFailed') : t('statusCancelled')) + (m.error_message ? `: ${m.error_message}` : m.stage_text ? `: ${m.stage_text}` : '');
    document.getElementById('workspace-progress-pct').textContent = '';
    bar.style.width = '100%';
    bar.style.background = failed ? 'rgba(255, 69, 58, 0.7)' : 'rgba(255, 255, 255, 0.2)';
  }
}

async function retryActiveMeeting() {
  const m = state.activeMeeting;
  if (!m) return;
  const res = await fetch(`/api/meetings/${m.id}/retry`, { method: 'POST' });
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
  await loadMeetingsList();
  await selectMeeting(m.id, false);
}

// --- Audio track switch (original / AI dubs) and dub export ---
function availableDubs(m) {
  return ((m && m.dubs) || []).filter(d => d && d.lang);
}

function renderAudioTrackSwitch() {
  const box = document.getElementById('audio-track-switch');
  if (!box) return;
  const m = state.activeMeeting;
  const dubs = availableDubs(m);
  if (state.audioTrack !== 'orig' && !dubs.some(d => d.lang === state.audioTrack)) state.audioTrack = 'orig';
  box.classList.toggle('hidden', !dubs.length);
  if (!dubs.length) { box.innerHTML = ''; renderDubExportMenu(); return; }
  const onDub = state.audioTrack !== 'orig';
  const option = (key, label) =>
    `<option value="${escapeHtml(key)}" ${state.audioTrack === key ? 'selected' : ''} class="bg-apple-card text-white">${escapeHtml(label)}</option>`;
  box.innerHTML = `
    <label class="ui-btn ${onDub ? 'ui-btn-active' : 'ui-btn-subtle'} pr-1 cursor-pointer" title="${escapeHtml(t('tipAudioTrack'))}">
      <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75z"/></svg>
      <select onchange="setAudioTrack(this.value)" class="bg-transparent focus:outline-none cursor-pointer text-[11.5px]">
        ${option('orig', t('trackOriginal'))}
        ${dubs.map(d => option(d.lang, `${t('trackDub')} ${d.lang.toUpperCase()}`)).join('')}
      </select>
    </label>`;
  renderDubExportMenu();
}

// Switches the player between the original file and a dubbed file, keeping position and play state
function setAudioTrack(key) {
  const m = state.activeMeeting;
  if (!m || key === state.audioTrack) return;
  const player = document.getElementById('media-player');
  const pos = player.currentTime || 0;
  const wasPlaying = !player.paused;
  state.audioTrack = key;
  player.src = key === 'orig' ? `/api/meetings/${m.id}/media` : `/api/meetings/${m.id}/dub/${encodeURIComponent(key)}/file`;
  player.addEventListener('loadedmetadata', () => {
    player.currentTime = Math.min(pos, player.duration || pos);
    if (wasPlaying) player.play().catch(() => {});
  }, { once: true });
  renderAudioTrackSwitch();
  showToast(key === 'orig' ? t('trackOriginalOn') : t('trackDubOn').replace('{lang}', dubLangName(key)));
}

function renderDubExportMenu() {
  if (!document.getElementById('export-menu').classList.contains('hidden')) renderExportMenu();
}

async function saveDubFile(lang, kind) {
  document.getElementById('export-menu')?.classList.add('hidden');
  const m = state.activeMeeting;
  if (!m) return;
  try {
    const res = await fetch(`/api/meetings/${m.id}/dub/${encodeURIComponent(lang)}/save/${kind}`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.detail || t('exportFailed')); return; }
    if (data.cancelled) return;
    showToast(`${t('exportSavedTo')} ${data.filename}`, { label: t('showInFolder'), title: t('tipShowInFolder'), onClick: () => revealPath(data.path) });
  } catch (e) {
    console.error(e);
    showToast(t('exportFailed'));
  }
}

// --- Translate & Dub (prototype) ---
function dubLangName(code) {
  const names = t('dubLangNames');
  return (names && names[code]) || String(code).toUpperCase();
}

async function openDubModal(preferLang) {
  const m = state.activeMeeting;
  if (!m) return;
  // Opened from the export menu: the file is saved after the dub; from the Translate menu: nothing pending
  if (!preferLang) state.pendingDubExport = null;
  document.getElementById('dub-consent').checked = false;
  const ds = (state.system && state.system.settings) || {};
  document.getElementById('dub-denoise').checked = ds.dub_denoise === 'true';
  document.getElementById('dub-roomtone').checked = ds.dub_room_tone !== 'false';
  syncDubNoiseControls('dub');

  let env = { installed: false, languages: Object.keys(I18N.ru.dubLangNames) };
  try { env = await (await fetch('/api/dub/status')).json(); } catch (e) { console.error(e); }
  renderDubSetup(env, 'dub');
  if (env.setup && env.setup.active) pollDubSetup();

  const srcLang = m.language || '';
  const preferred = srcLang === 'en' ? 'ru' : 'en';
  document.getElementById('dub-lang').innerHTML = env.languages
    .filter(c => c !== srcLang)
    .map(c => ({ c, n: dubLangName(c) }))
    .sort((a, b) => (a.c === preferred ? -1 : b.c === preferred ? 1 : a.n.localeCompare(b.n)))
    .map(({ c, n }) => `<option value="${c}">${escapeHtml(n)} (${c.toUpperCase()})</option>`).join('');
  if (preferLang) document.getElementById('dub-lang').value = preferLang;

  // The server reports a failed dub in stage_text and puts the recording back to "completed"
  const st = m.stage_text || '';
  const isDubError = m.status === 'completed' && st.startsWith('Ошибка дубляжа');
  const errBox = document.getElementById('dub-last-error');
  errBox.classList.toggle('hidden', !isDubError);
  errBox.textContent = isDubError ? st : '';

  renderDubResults();
  updateDubStartButton();
  openModal('dub');
}

// Optional dubbing environment (.venv-dub): installed on request from the Dub window ('dub') or
// from Settings → Speech synthesis ('sdub'); both show the same setup state
function renderDubSetup(env, prefix = 'dub') {
  const el = (id) => document.getElementById(`${prefix}-${id}`);
  if (!el('setup-btn')) return;
  const st = env.setup || {};
  state.dubSetup = st;
  state.dubEnvInstalled = !!env.installed;
  if (prefix === 'dub') {
    document.getElementById('dub-env-panel').classList.toggle('hidden', !!env.installed);
    if (env.installed) return;
  }

  const meta = [t('dubSetupSize').replace('{gb}', st.download_gb ?? 6)];
  if (st.free_gb != null) meta.push(t('dubSetupFree').replace('{gb}', st.free_gb));
  el('setup-meta').textContent = env.installed && !st.active ? '' : meta.join(' · ');

  const logs = st.logs || [];
  el('setup-progress').classList.toggle('hidden', !st.active && !logs.length);
  el('setup-step').textContent =
    st.active ? (t('dubSetupStep_' + st.step) || '') : (st.error ? t('dubSetupFailed') : (st.done ? t('dubSetupDone') : ''));
  el('setup-pct').textContent = `${st.progress || 0}%`;
  el('setup-bar').style.width = `${st.progress || 0}%`;
  const logsBox = el('setup-logs');
  logsBox.innerHTML = logs.map(l => `<div>${escapeHtml(l)}</div>`).join('');
  logsBox.scrollTop = logsBox.scrollHeight;

  const errBox = el('setup-error');
  errBox.textContent = st.error || '';
  errBox.classList.toggle('hidden', !st.error);

  const btn = el('setup-btn');
  btn.textContent = env.installed ? t('sdubRepairBtn') : (st.error ? t('dubSetupRetry') : t('dubSetupBtn'));
  btn.title = env.installed ? t('tipSdubRepair') : '';
  btn.className = `ui-btn ${env.installed ? 'ui-btn-subtle' : 'ui-btn-primary'} h-7 px-3 text-[11px]`;
  btn.classList.toggle('hidden', !!st.active);
  el('setup-cancel-btn').classList.toggle('hidden', !st.active);
  el('setup-logs-btn').classList.toggle('hidden', !logs.length);
}

function toggleDubSetupLogs(prefix = 'dub') {
  document.getElementById(`${prefix}-setup-logs`).classList.toggle('hidden');
}

async function startDubSetup() {
  const st = state.dubSetup || {};
  const question = (state.dubEnvInstalled ? t('sdubRepairConfirm') : t('dubSetupConfirm'))
    .replace('{gb}', st.download_gb ?? 6)
    .replace('{dir}', st.venv || '.venv-dub');
  if (!confirm(question)) return;
  let res = null;
  try { res = await fetch('/api/dub/setup', { method: 'POST' }); } catch (e) { console.error(e); }
  const setup = res ? await res.json().catch(() => ({})) : {};
  if (!res || !res.ok) {
    // e.g. an older server process is still running behind this window
    const code = res ? res.status : 0;
    const failed = { installed: false, setup: { ...st, active: false, error: t('dubSetupRequestFailed').replace('{code}', code || '—') } };
    renderDubSetup(failed, 'dub');
    renderDubSetup(failed, 'sdub');
    return;
  }
  const env = { installed: false, setup };
  renderDubSetup(env, 'dub');
  renderDubSetup(env, 'sdub');
  if (setup.active) pollDubSetup();
}

async function cancelDubSetup() {
  await fetch('/api/dub/setup/cancel', { method: 'POST' });
}

async function pollDubSetup() {
  if (state.dubSetupPolling) return;
  state.dubSetupPolling = true;
  try {
    for (;;) {
      await new Promise(r => setTimeout(r, 1500));
      const env = await (await fetch('/api/dub/status')).json();
      renderDubSetup(env, 'dub');
      renderDubSetup(env, 'sdub');
      updateDubStartButton();
      if (!env.setup || !env.setup.active) {
        if (env.installed) showToast(t('dubSetupDone'));
        loadDubInfo();
        break;
      }
    }
  } catch (e) {
    console.error(e);
  } finally {
    state.dubSetupPolling = false;
  }
}

// --- Settings → Speech synthesis (dub): environment, weights, disk usage, options ---
function formatBytes(n) {
  if (n == null) return '…';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} ${t('unitGB')}`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} ${t('unitMB')}`;
  return `${Math.round(n / 1e3)} ${t('unitKB')}`;
}

async function loadDubInfo() {
  try {
    const res = await fetch('/api/dub/info');
    if (!res.ok) return;
    renderDubInfo(await readJson(res));
  } catch (e) {
    console.error(e);
  }
}

function renderDubInfo(info) {
  state.dubInfo = info;
  const st = info.setup || {};
  const pill = document.getElementById('sdub-env-pill');
  const [pillText, pillCls] = st.active ? [t('sdubPillInstalling'), 'bg-apple-blue/15 text-apple-blue']
    : info.installed ? [t('sdubPillReady'), 'bg-apple-green/15 text-apple-green']
    : info.venv_exists ? [t('sdubPillBroken'), 'bg-apple-amber/15 text-apple-amber']
    : [t('sdubPillMissing'), 'bg-white/5 text-apple-secondary'];
  pill.textContent = pillText;
  pill.className = `text-[10px] font-mono px-2.5 py-0.5 rounded-full shrink-0 ${pillCls}`;

  const detail = [info.venv];
  if (info.verified) detail.push(info.verified.replace(/^Verified/, t('sdubVerifiedAt')));
  else if (info.venv_exists) detail.push(t('sdubNotVerified'));
  else detail.push(t('sdubNotInstalled'));
  document.getElementById('sdub-env-detail').textContent = detail.join(' · ');
  document.getElementById('sdub-verify-btn').classList.toggle('hidden', !info.venv_exists || !!st.active);
  renderDubSetup(info, 'sdub');

  // Model weights
  const w = info.weights || { items: [], download: {} };
  const dl = w.download || {};
  const busy = !!dl.active || !!info.dub_running;
  document.getElementById('sdub-weights-list').innerHTML = w.items.map(item => {
    const downloading = dl.active && (dl.models || []).includes(item.id);
    let action;
    if (downloading) {
      action = `<span class="ui-btn bg-apple-blue/20 text-apple-blue font-mono animate-pulse">${dl.progress || 1}%</span>`;
    } else if (item.installed) {
      action = `<span class="ui-btn bg-apple-green/20 text-apple-green">${SVG_ICONS.check}<span>${escapeHtml(t('sdubWeightsReady'))}</span></span>
        <button data-id="${escapeHtml(item.id)}" onclick="deleteDubWeights(this.dataset.id)" ${busy ? 'disabled style="opacity:.4"' : ''}
          title="${escapeHtml(t('tipDeleteModel'))}" class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button>`;
    } else {
      action = `<button data-id="${escapeHtml(item.id)}" onclick="downloadDubWeights([this.dataset.id])" ${busy || !info.installed ? 'disabled style="opacity:.4"' : ''}
          title="${escapeHtml(info.installed ? t('tipDownloadModel') : t('sdubNeedEnv'))}" class="ui-btn ui-btn-primary">${SVG_ICONS.download}<span>${escapeHtml(t('downloadBtn'))} (${escapeHtml(formatBytes(item.expected_bytes))})</span></button>`;
    }
    const size = item.size_bytes ? formatBytes(item.size_bytes) : t('sdubNotDownloaded');
    return `<div class="p-3 rounded-xl border bg-apple-sidebar border-white/10 flex items-center justify-between gap-3">
      <div class="min-w-0">
        <div class="flex items-center flex-wrap gap-2">
          <span class="text-xs font-semibold text-white">${escapeHtml(item.name)}</span>
          <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">${escapeHtml(item.license)}</span>
          <span class="text-[10px] text-apple-tertiary">${escapeHtml(size)}</span>
        </div>
        <p class="text-[11.5px] text-apple-secondary mt-1 leading-snug">${escapeHtml(t('sdubWeightDesc_' + item.id))}</p>
      </div>
      <div class="shrink-0 flex items-center gap-1.5">${action}</div>
    </div>`;
  }).join('');
  document.getElementById('sdub-weights-progress').classList.toggle('hidden', !dl.active);
  if (dl.active) {
    document.getElementById('sdub-weights-step').textContent = t('sdubWeightsDownloading');
    document.getElementById('sdub-weights-pct').textContent = `${dl.progress || 0}%`;
    document.getElementById('sdub-weights-bar').style.width = `${dl.progress || 0}%`;
  }
  const err = document.getElementById('sdub-weights-error');
  err.textContent = info.error || dl.error || '';
  err.classList.toggle('hidden', !(info.error || dl.error));

  const d = info.disk || {};
  document.getElementById('sdub-disk').textContent = t('sdubDisk')
    .replace('{env}', formatBytes(d.env_bytes))
    .replace('{weights}', formatBytes(d.weights_bytes))
    .replace('{outputs}', formatBytes(d.outputs_bytes))
    .replace('{free}', formatBytes(d.free_bytes));

  // Rubber Band is only offered when this FFmpeg build has the filter
  const rb = document.querySelector('#set-dub-stretch option[value="rubberband"]');
  rb.disabled = !info.rubberband;
  rb.textContent = info.rubberband ? 'Rubber Band' : `Rubber Band — ${t('sdubNoRubberband')}`;
  clearTimeout(state.dubInfoTimer);
  const modalOpen = !document.getElementById('modal-settings').classList.contains('hidden');
  if (dl.active && modalOpen) state.dubInfoTimer = setTimeout(loadDubInfo, 1500);
}

async function downloadDubWeights(ids) {
  const items = ((state.dubInfo && state.dubInfo.weights && state.dubInfo.weights.items) || [])
    .filter(i => !i.installed && (!ids || ids.includes(i.id)));
  if (!items.length) return;
  const total = items.reduce((a, i) => a + (i.expected_bytes || 0), 0);
  const question = t('sdubDownloadConfirm')
    .replace('{names}', items.map(i => i.name).join(', '))
    .replace('{size}', formatBytes(total))
    .replace('{dir}', state.dubInfo.weights.dir);
  if (!confirm(question)) return;
  const res = await fetch('/api/dub/weights/download', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ models: items.map(i => i.id) }),
  });
  const data = await res.json().catch(() => ({}));
  if (data.error) showToast(data.error);
  loadDubInfo();
}

async function cancelDubWeights() {
  await fetch('/api/dub/weights/cancel', { method: 'POST' });
  loadDubInfo();
}

async function deleteDubWeights(id) {
  const item = ((state.dubInfo && state.dubInfo.weights.items) || []).find(i => i.id === id);
  if (!item || !confirm(t('sdubDeleteConfirm').replace('{name}', item.name).replace('{size}', formatBytes(item.size_bytes)))) return;
  const res = await fetch(`/api/dub/weights/${encodeURIComponent(id)}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (data.error) showToast(data.error);
  loadDubInfo();
}

async function verifyDubEnv() {
  const btn = document.getElementById('sdub-verify-btn');
  btn.disabled = true;
  btn.textContent = t('sdubVerifying');
  try {
    const res = await fetch('/api/dub/verify', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    showToast(res.ok ? (data.ok ? t('sdubVerifyOk') : t('sdubVerifyFailed')) : (data.detail || 'Error'));
  } finally {
    btn.disabled = false;
    btn.textContent = t('sdubVerifyBtn');
    loadDubInfo();
  }
}

// "Remove noise" makes room tone pointless (it would add the noise back), so it is disabled then
function syncDubNoiseControls(prefix) {
  const denoise = document.getElementById(prefix === 'set' ? 'set-dub-denoise' : 'dub-denoise');
  const room = document.getElementById(prefix === 'set' ? 'set-dub-roomtone' : 'dub-roomtone');
  if (!denoise || !room) return;
  room.disabled = denoise.checked;
  room.closest('label').style.opacity = denoise.checked ? '0.45' : '';
}

function fillDubSettings(s) {
  const val = (k, d) => (s[k] == null || s[k] === '' ? d : s[k]);
  document.getElementById('set-dub-workers').value = val('dub_tts_workers', 'auto');
  document.getElementById('set-dub-stretch').value = val('dub_stretch', 'atempo');
  const cfg = Number(val('dub_cfg_weight', '0.3'));
  const exag = Number(val('dub_exaggeration', '0.5'));
  document.getElementById('set-dub-cfg').value = cfg;
  document.getElementById('set-dub-cfg-val').textContent = cfg.toFixed(2);
  document.getElementById('set-dub-exag').value = exag;
  document.getElementById('set-dub-exag-val').textContent = exag.toFixed(2);
  document.getElementById('set-dub-denoise').checked = val('dub_denoise', 'false') === 'true';
  document.getElementById('set-dub-roomtone').checked = val('dub_room_tone', 'true') === 'true';
  syncDubNoiseControls('set');
}

function dubSettingsPayload() {
  return {
    dub_tts_workers: document.getElementById('set-dub-workers').value,
    dub_stretch: document.getElementById('set-dub-stretch').value,
    dub_cfg_weight: String(document.getElementById('set-dub-cfg').value),
    dub_exaggeration: String(document.getElementById('set-dub-exag').value),
    dub_denoise: String(document.getElementById('set-dub-denoise').checked),
    dub_room_tone: String(document.getElementById('set-dub-roomtone').checked),
  };
}

function updateDubStartButton() {
  const m = state.activeMeeting;
  const ok = !!m && m.status === 'completed' && state.dubEnvInstalled && document.getElementById('dub-consent').checked;
  const btn = document.getElementById('dub-start-btn');
  btn.disabled = !ok;
  btn.style.opacity = ok ? '' : '0.4';
  btn.style.cursor = ok ? '' : 'not-allowed';
}

function renderDubResults() {
  const m = state.activeMeeting;
  const box = document.getElementById('dub-results');
  const dubs = (m && m.dubs) || [];
  if (!dubs.length) {
    box.innerHTML = `<div class="text-[11px] text-apple-tertiary">${escapeHtml(t('dubNone'))}</div>`;
    return;
  }
  box.innerHTML = dubs.map(d => {
    const name = String(d.path || '').split(/[\\/]/).pop();
    const isVideo = /\.(mp4|mkv)$/i.test(name);
    const lang = encodeURIComponent(d.lang);
    const src = `/api/meetings/${m.id}/dub/${lang}/file`;
    const s = d.stats || {};
    const meta = [d.created_at, s.units ? `${s.units} ${t('dubPhrases')}` : '', s.elapsed_sec ? `${Math.round(s.elapsed_sec)} ${t('dubSec')}` : '',
      s.denoise ? t('dubMetaDenoised') : (s.room_tone_db != null ? t('dubMetaRoomTone') : '')]
      .filter(Boolean).join(' · ');
    return `<div class="rounded-lg bg-white/[0.03] border apple-border p-2.5 space-y-2">
      <div class="flex items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="text-xs font-semibold text-white">${escapeHtml(dubLangName(d.lang))} · <span class="text-apple-amber">AI dub</span></div>
          <div class="text-[10.5px] text-apple-secondary truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
          <div class="text-[10.5px] text-apple-tertiary">${escapeHtml(meta)}</div>
        </div>
        <button onclick="revealDub('${lang}')" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px] shrink-0">${escapeHtml(t('dubReveal'))}</button>
      </div>
      ${isVideo
        ? `<video controls preload="none" class="w-full rounded-md bg-black max-h-56" src="${src}"></video>`
        : `<audio controls preload="none" class="w-full" src="${src}"></audio>`}
    </div>`;
  }).join('');
}

async function startDub() {
  const m = state.activeMeeting;
  if (!m) return;
  // The noise options are remembered as settings (also editable in Settings → Speech synthesis)
  const noise = {
    dub_denoise: String(document.getElementById('dub-denoise').checked),
    dub_room_tone: String(document.getElementById('dub-roomtone').checked),
  };
  try {
    await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: noise }) });
    if (state.system && state.system.settings) Object.assign(state.system.settings, noise);
  } catch (e) { console.error(e); }
  const res = await fetch(`/api/meetings/${m.id}/dub`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_lang: document.getElementById('dub-lang').value, consent: document.getElementById('dub-consent').checked }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  closeModal('dub');
  const pending = state.pendingDubExport;
  state.pendingDubExport = null;
  if (pending && pending.lang === document.getElementById('dub-lang').value) {
    // The export that asked for this dub is saved when the dub is ready
    try {
      const r = await fetch(`/api/meetings/${m.id}/export-request`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pending),
      });
      const d = await r.json().catch(() => ({}));
      showToast(r.ok && d.status === 'queued' ? t('exportQueued').replace('{what}', t('exportWaiting_dub')).replace('{dir}', d.folder || '') : t('dubQueued'));
    } catch (e) {
      showToast(t('dubQueued'));
    }
  } else {
    showToast(t('dubQueued'));
  }
  await loadMeetingsList();
}

async function revealDub(lang) {
  const m = state.activeMeeting;
  if (!m) return;
  const res = await fetch(`/api/meetings/${m.id}/dub/${lang}/reveal`, { method: 'POST' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.detail || 'Error');
  }
}

// --- Translate text ---
const VIDEO_EXT_RE = /\.(mp4|m4v|mov|mkv|webm|avi)$/i;

// Languages for text and live translation (app/translation.py TEXT_LANGUAGES), loaded once
const TEXT_LANG_FALLBACK = ['ar', 'bg', 'cs', 'da', 'de', 'el', 'en', 'es', 'et', 'fa', 'fi', 'fr', 'he', 'hi', 'hu',
  'hy', 'id', 'it', 'ja', 'ka', 'kk', 'ko', 'lt', 'lv', 'ms', 'nl', 'no', 'pl', 'pt', 'ro', 'ru', 'sk', 'sr', 'sr-latn',
  'sv', 'sw', 'th', 'tr', 'uk', 'uz', 'vi', 'zh'];
let _textLangs = null;

async function textLanguages() {
  if (_textLangs) return _textLangs;
  try {
    const r = await fetch('/api/translate/languages');
    if (r.ok) _textLangs = (await r.json()).languages;
  } catch (e) {}
  return _textLangs || TEXT_LANG_FALLBACK;
}

function textLangOptions(codes, excludeScripts) {
  return codes.filter(c => !(excludeScripts && c.includes('-')))
    .map(c => ({ c, n: langName(c) }))
    .sort((a, b) => a.n.localeCompare(b.n))
    .map(({ c, n }) => `<option value="${c}">${escapeHtml(n)}</option>`).join('');
}

function langName(code) {
  // Serbian is written in two scripts: say which one
  if (code === 'sr') code = 'sr-Cyrl';
  if (code === 'sr-latn') code = 'sr-Latn';
  try {
    const n = new Intl.DisplayNames([state.uiLang], { type: 'language' }).of(code);
    return n ? n.charAt(0).toUpperCase() + n.slice(1) : String(code).toUpperCase();
  } catch (e) {
    return String(code).toUpperCase();
  }
}

function meetingTranslations(m) {
  return (m && m.translations) || {};
}

async function openTranslateModal() {
  const m = state.activeMeeting;
  if (!m) return;
  // Show the window at once; languages and models are filled in when the server answers
  renderTranslateResults();
  renderSubsControls();
  openModal('translate');
  let langs = [];
  let ollama = { running: false, models: [], active_model: '' };
  try {
    const [l, o] = await Promise.all([fetch('/api/translate/languages'), fetch('/api/ollama/status')]);
    langs = (await l.json()).languages || [];
    ollama = await o.json();
  } catch (e) { console.error(e); }

  const srcLang = m.language || '';
  const have = Object.keys(meetingTranslations(m));
  const preferred = srcLang === 'en' ? 'ru' : 'en';
  document.getElementById('translate-lang').innerHTML = langs
    .filter(c => c !== srcLang)
    .map(c => ({ c, n: langName(c) }))
    .sort((a, b) => (a.c === preferred ? -1 : b.c === preferred ? 1 : a.n.localeCompare(b.n)))
    .map(({ c, n }) => `<option value="${c}">${escapeHtml(n)} (${c.toUpperCase()})${have.includes(c) ? ' ✓' : ''}</option>`).join('');

  const models = ollama.models || [];
  const active = (state.system && state.system.settings && state.system.settings.ollama_model) || ollama.active_model || '';
  document.getElementById('translate-model').innerHTML = models.length
    ? models.map(x => `<option value="${escapeHtml(x)}" ${x === active ? 'selected' : ''}>${escapeHtml(x)}</option>`).join('')
    : `<option value="">${escapeHtml(active || '—')}</option>`;
  // Ollama is started on demand by the job, so a stopped Ollama is only a hint, not a blocker
  document.getElementById('translate-no-ollama').classList.toggle('hidden', !!(ollama.running && models.length));

  const st = m.stage_text || '';
  const isErr = m.status === 'completed' && st.startsWith('Ошибка (перевод)');
  const errBox = document.getElementById('translate-last-error');
  errBox.classList.toggle('hidden', !isErr);
  errBox.textContent = isErr ? st : '';

  const ok = m.status === 'completed' && (m.segments || []).length > 0;
  const btn = document.getElementById('translate-start-btn');
  btn.disabled = !ok;
  btn.style.opacity = ok ? '' : '0.4';

}

function renderTranslateResults() {
  const m = state.activeMeeting;
  const box = document.getElementById('translate-results');
  const trs = meetingTranslations(m);
  const langs = Object.keys(trs);
  if (!langs.length) {
    box.innerHTML = `<div class="text-[11px] text-apple-tertiary">${escapeHtml(t('translateNone'))}</div>`;
    return;
  }
  box.innerHTML = langs.map(lang => {
    const r = trs[lang];
    const outdated = (r.stale || 0) + (r.missing || 0);
    const meta = [`${r.translated}/${r.count} ${t('translateSegments')}`, r.model, r.updated_at].filter(Boolean).join(' · ');
    return `<div class="rounded-lg bg-white/[0.03] border apple-border p-2.5 space-y-1.5">
      <div class="flex items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="text-xs font-semibold text-white">${escapeHtml(langName(lang))} (${escapeHtml(lang.toUpperCase())})</div>
          <div class="text-[10.5px] text-apple-tertiary truncate">${escapeHtml(meta)}</div>
          ${outdated ? `<div class="text-[10.5px] text-apple-amber">${escapeHtml(t('translateOutdated').replace('{stale}', r.stale || 0).replace('{missing}', r.missing || 0))}</div>` : ''}
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button onclick="showTranslation('${lang}')" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('translateShow'))}</button>
          ${outdated ? `<button onclick="startTranslation(true, '${lang}')" class="ui-btn ui-btn-primary h-6 px-2 text-[11px]" title="${escapeHtml(t('tipTranslateUpdate'))}">${escapeHtml(t('translateUpdate'))}</button>` : ''}
          <button onclick="startTranslation(false, '${lang}')" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]" title="${escapeHtml(t('tipTranslateRedo'))}">${escapeHtml(t('translateRedo'))}</button>
          <button onclick="deleteTranslation('${lang}')" class="ui-btn ui-btn-subtle h-6 px-1.5 text-[11px] hover:text-apple-red" title="${escapeHtml(t('tipTranslateDelete'))}">${SVG_ICONS.trash}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function renderSubsControls() {
  const m = state.activeMeeting;
  const langs = Object.keys(meetingTranslations(m));
  const sel = document.getElementById('subs-lang');
  const prev = sel.value;
  sel.innerHTML = langs.map(c => `<option value="${c}">${escapeHtml(t('subsLangTranslation').replace('{lang}', langName(c)))}</option>`).join('')
    + `<option value="">${escapeHtml(t('subsLangOriginal'))}</option>`;
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  updateSubsControls();
}

function updateSubsControls() {
  const m = state.activeMeeting;
  const isVideo = !!m && VIDEO_EXT_RE.test(m.filename || m.media_path || '');
  const lang = document.getElementById('subs-lang').value;
  const variant = document.getElementById('subs-variant');
  variant.disabled = !lang;
  variant.style.opacity = lang ? '' : '0.4';
  for (const id of ['subs-soft-btn', 'subs-burn-btn']) {
    const b = document.getElementById(id);
    b.disabled = !isVideo;
    b.style.opacity = isVideo ? '' : '0.4';
  }
  document.getElementById('subs-no-video').classList.toggle('hidden', isVideo);
}

async function startTranslation(onlyChanged, lang) {
  const m = state.activeMeeting;
  if (!m) return;
  const target = lang || document.getElementById('translate-lang').value;
  const res = await fetch(`/api/meetings/${m.id}/translate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_lang: target, model: document.getElementById('translate-model').value || null, only_changed: !!onlyChanged }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  state.txLang = target;
  closeModal('translate');
  showToast(t('translateQueued'));
  await loadMeetingsList();
}

async function deleteTranslation(lang) {
  const m = state.activeMeeting;
  if (!m || !confirm(t('translateDeleteConfirm').replace('{lang}', langName(lang)))) return;
  const res = await fetch(`/api/meetings/${m.id}/translations/${encodeURIComponent(lang)}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  state.activeMeeting = data;
  renderTranslateResults();
  renderSubsControls();
  renderTranslationControls();
  renderTranscript();
}

function showTranslation(lang) {
  state.txLang = lang;
  closeModal('translate');
  switchWorkspaceTab('transcript');
  setTranscriptView(state.txView === 'orig' ? 'both' : state.txView);
}

async function makeSubtitles(mode) {
  const m = state.activeMeeting;
  if (!m) return;
  const lang = document.getElementById('subs-lang').value;
  const variant = lang ? document.getElementById('subs-variant').value : 'translated';
  if (mode === 'srt') {
    if (!lang) { await exportMeetingById(m.id, 'srt'); return; }
    await exportMeetingById(m.id, 'srt', { lang, variant });
    return;
  }
  const done = await requestVideoSubtitles(lang, variant, mode, document.getElementById('subs-ask-path').checked);
  if (done) closeModal('translate');
}

// A copy of the video with subtitles: "soft" adds a subtitle track at once, "burn" re-encodes in the queue
async function requestVideoSubtitles(lang, variant, mode, askPath) {
  const m = state.activeMeeting;
  if (!m) return false;
  if (mode === 'burn' && !confirm(t('subsBurnConfirm'))) return false;
  showToast(mode === 'soft' ? t('subsSoftWorking') : t('subsBurnWorking'));
  const res = await fetch(`/api/meetings/${m.id}/subtitles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lang: lang || null, variant, mode, ask_path: !!askPath }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return false;
  }
  if (data.cancelled) return false;
  if (data.queued) {
    showToast(t('subsBurnQueued').replace('{name}', data.filename));
    await loadMeetingsList();
    return true;
  }
  showToast(`${t('exportSavedTo')} ${data.filename}`, { label: t('dubReveal'), onClick: () => revealPath(data.path) });
  return true;
}

// Transcript view: original / translation / both
function renderTxQuickLangs() {
  const m = state.activeMeeting;
  const box = document.getElementById('tx-quick-langs');
  if (!box) return;
  if (!m || !(m.segments || []).length) { box.innerHTML = ''; return; }
  const have = meetingTranslations(m);
  const src = String(m.language || '').slice(0, 2);
  const showing = state.txView && state.txView !== 'orig' ? state.txLang : null;
  box.innerHTML = quickLangs().filter(c => c !== src).map(c => {
    const ready = !!have[c];
    const on = ready && showing === c;
    return `<button onclick="${on ? "setTranscriptView('orig')" : `quickTranslate('${c}')`}"
      title="${escapeHtml(on ? t('subsLangOriginal') : ready ? t('translateQuickShow').replace('{lang}', langName(c)) : t('translateQuickMake').replace('{lang}', langName(c)))}"
      class="quick-lang ${on ? 'on' : ''}">${ready && !on ? '✓ ' : ''}${escapeHtml(langCode(c))}</button>`;
  }).join('');
}

function renderTranslationControls() {
  renderTxQuickLangs();
  const m = state.activeMeeting;
  const langs = Object.keys(meetingTranslations(m));
  // Always offered for finished recordings: the language list also starts a new translation
  const canTranslate = !!m && m.status === 'completed' && (m.segments || []).length > 0;
  document.getElementById('tx-view-controls').classList.toggle('hidden', !canTranslate && !langs.length);
  if (langs.length && !langs.includes(state.txLang)) state.txLang = langs[0];
  if (!langs.length && state.txView && state.txView !== 'orig') state.txView = 'orig';
  const view = state.txView || 'orig';
  const src = ((m && m.language) || '').toUpperCase();
  const tr = (state.txLang || '').toUpperCase();
  document.getElementById('tx-view-label').textContent =
    view === 'tr' ? tr : view === 'both' ? `${src || t('txViewOrig')} + ${tr}` : t('txViewOrig');
  document.getElementById('tx-view-btn').className = `ui-btn ${view === 'orig' ? 'ui-btn-subtle' : 'ui-btn-active'}`;

  const item = (onclick, mark, label) =>
    `<button onclick="${onclick}" class="macos-menu-item gap-2" style="justify-content:flex-start">`
    + `<span class="w-3 shrink-0 text-center">${mark}</span><span class="truncate">${escapeHtml(label)}</span></button>`;
  const sep = '<div class="h-px bg-white/10 my-1"></div>';
  let html = item("setTxViewChoice('orig')", view === 'orig' ? '✓' : '', src ? `${t('txViewOrig')} (${src})` : t('txViewOrig'));
  for (const c of langs) {
    const on = c === state.txLang;
    html += sep;
    html += item(`setTxViewChoice('tr','${c}')`, view === 'tr' && on ? '✓' : '', `${t('txViewTr')}: ${langName(c)}`);
    html += item(`setTxViewChoice('both','${c}')`, view === 'both' && on ? '✓' : '', `${t('txViewBothWith')} ${langName(c)}`);
  }
  if (canTranslate) html += sep + item("setTxViewChoice('__new')", '＋', t('txTranslateNew').replace(/^＋\s*/, ''));
  document.getElementById('tx-view-menu').innerHTML = html;
  renderExportTranslatedMenu();
}

function toggleTxViewMenu(e) {
  e.stopPropagation();
  hideContextMenu();
  hideTranslateMenu();
  document.getElementById('export-menu')?.classList.add('hidden');
  document.getElementById('tx-view-menu').classList.toggle('hidden');
}

function hideTxViewMenu() {
  document.getElementById('tx-view-menu')?.classList.add('hidden');
}

function setTxViewChoice(view, lang) {
  hideTxViewMenu();
  if (view === '__new') {
    openTranslateModal();
    return;
  }
  if (lang) state.txLang = lang;
  setTranscriptView(view);
}

function setTranscriptView(view) {
  state.txView = view;
  try { localStorage.setItem('verbascribe_tx_view', view); } catch (e) {}
  renderTranslationControls();
  renderTranscript();
  updateTheatreCaption(document.getElementById('media-player').currentTime || 0, true);
}

function setTranscriptViewLang(lang) {
  if (lang === '__new') {
    renderTranslationControls();  // put the selection back
    openTranslateModal();
    return;
  }
  state.txLang = lang;
  renderTranslationControls();
  renderTranscript();
}

// Translation of a segment for the current view, or null in "original" view
function activeSegmentTranslation(seg) {
  if (!state.txView || state.txView === 'orig') return null;
  const tr = meetingTranslations(state.activeMeeting)[state.txLang];
  if (!tr) return null;
  return tr.segments[String(seg.id)] || { text: '', stale: false, missing: true };
}

function renderExportTranslatedMenu() {
  renderDubExportMenu();
}

async function cancelActiveProcessing() {
  if (!state.activeMeeting) return;
  await cancelProcessing(state.activeMeeting.id);
}

async function cancelProcessing(id) {
  const res = await fetch(`/api/meetings/${id}/cancel`, { method: 'POST' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.detail || 'Error');
    return;
  }
  showToast(state.uiLang === 'en' ? 'Cancelling…' : 'Обработка отменяется…');
  await loadMeetingsList();
}

async function saveMeetingTitle() {
  if (!state.activeMeeting) return;
  const newTitle = document.getElementById('meeting-title-input').value.trim();
  if (!newTitle || newTitle === state.activeMeeting.title) return;

  await fetch(`/api/meetings/${state.activeMeeting.id}/title`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle })
  });
  state.activeMeeting.title = newTitle;
  await loadMeetingsList();
}

// Inline Speaker Pills Bar inside Transcript Sub-Toolbar
function renderSpeakerPillsBar() {
  const container = document.getElementById('speaker-pills-container');
  const speakers = (state.activeMeeting && state.activeMeeting.speakers) || {};
  const keys = Object.keys(speakers);

  if (keys.length === 0) {
    container.innerHTML = '';
    return;
  }

  // Compact: a speaker icon for "everyone" and a numbered circle in each speaker's colour; the
  // name is in the tooltip, right-click plays a sample of the voice
  const allActive = !state.speakerFilter;
  let html = `<button onclick="filterBySpeaker(null)" title="${escapeHtml(t('tipFilterAllSpeakers'))}"
      class="spk-chip ${allActive ? 'spk-chip-on' : ''}">${ICON_SPEAKERS}</button>`;
  html += keys.map((spkId, i) => {
    const s = speakers[spkId];
    const active = state.speakerFilter === spkId;
    const named = !/^(Участник|Speaker|SPEAKER)[ _]?\d+$/i.test(s.name || '');
    const label = named ? (s.name || '?').trim().slice(0, 1).toUpperCase() : String(i + 1);
    return `<button onclick="filterBySpeaker('${spkId}')" oncontextmenu="event.preventDefault(); previewSpeakerVoice(${s.sample_start || 0}, ${s.sample_end || 5})"
      title="${escapeHtml(`${s.name}\n${t('tipFilterSpeaker')} · ${t('tipSpeakerChipVoice')}`)}"
      class="spk-chip ${active ? 'spk-chip-on' : ''}" style="--spk:${s.color}">${escapeHtml(label)}</button>`;
  }).join('');

  container.innerHTML = html;
}

async function renderTalkAnalytics() {
  const box = document.getElementById('talk-analytics');
  const m = state.activeMeeting;
  if (!box || !m || !(m.segments || []).length) { if (box) box.innerHTML = ''; return; }
  const a = await fetch(`/api/meetings/${m.id}/analytics`).then(r => r.ok ? r.json() : null).catch(() => null);
  if (!a || state.activeMeeting !== m) return;
  const min = s => s >= 60 ? `${Math.floor(s / 60)} ${t('unitMin')} ${Math.round(s % 60)} ${t('unitSec')}` : `${Math.round(s)} ${t('unitSec')}`;
  const stat = (label, value) => `<div class="rounded-lg bg-white/[0.03] border apple-border px-3 py-2"><div class="text-[10.5px] text-apple-tertiary">${escapeHtml(label)}</div><div class="text-sm font-semibold text-white mt-0.5">${escapeHtml(value)}</div></div>`;
  box.innerHTML = `
    <h3 class="text-sm font-semibold text-white">${escapeHtml(t('analyticsTitle'))}</h3>
    <div class="grid grid-cols-3 gap-2">${stat(t('analyticsTalk'), min(a.talk_seconds))}${stat(t('analyticsSilence'), `${a.silence_share}%`)}${stat(t('analyticsTurns'), `${a.turns} · ${t('analyticsAvg')} ${min(a.avg_turn)}`)}</div>
    <div class="space-y-2.5 border apple-border rounded-xl bg-apple-sidebar p-3">
      ${a.speakers.map(r => `<div>
        <div class="flex items-center justify-between text-xs"><span class="font-medium" style="color:${r.color}">${escapeHtml(r.name)}</span><span class="font-mono text-apple-secondary">${r.share}% · ${min(r.seconds)}</span></div>
        <div class="h-1.5 rounded-full bg-white/5 mt-1 overflow-hidden"><div class="h-full rounded-full" style="width:${r.share}%;background:${r.color}"></div></div>
        <div class="text-[10.5px] text-apple-tertiary mt-1">${escapeHtml([
          `${r.turns} ${t('turns')}`, r.wpm ? `${r.wpm} ${t('analyticsWpm')}` : '', `${t('analyticsLongest')} ${min(r.longest)}`,
          r.questions ? `${r.questions} ${t('analyticsQuestions')}` : '', r.interruptions ? `${t('analyticsInterruptions')}: ${r.interruptions}` : '',
        ].filter(Boolean).join(' · '))}</div></div>`).join('')}
    </div>
    ${a.longest_monologues.length ? `<div class="text-[11px] text-apple-secondary">${escapeHtml(t('analyticsMonologues'))}: ${a.longest_monologues.map(x =>
      `<button onclick="seekPlayer(${x.start})" class="text-apple-blue hover:underline font-mono">${formatTime(x.start)}</button> ${escapeHtml(x.name)} (${min(x.seconds)})`).join(' · ')}</div>` : ''}`;
}

function renderSpeakersPanel() {
  renderTalkAnalytics();
  const box = document.getElementById('speakers-list');
  const speakers = (state.activeMeeting && state.activeMeeting.speakers) || {};
  const keys = Object.keys(speakers);

  if (keys.length === 0) {
    box.innerHTML = `<div class="p-6 text-xs text-apple-tertiary text-center">—</div>`;
    return;
  }

  box.innerHTML = keys.map(spkId => {
    const s = speakers[spkId];
    const turnCount = (state.activeMeeting.segments || []).filter(seg => seg.speaker === spkId).length;

    return `
      <div class="px-4 py-3 flex items-center gap-3">
        <input type="color" value="${s.color || '#0A84FF'}"
          title="${escapeHtml(t('tipSpeakerColor'))}"
          onchange="updateSpeaker('${spkId}', null, this.value)"
          class="w-4 h-4 rounded-full cursor-pointer bg-transparent border-0 shrink-0" />

        <input type="text" value="${escapeHtml(s.name)}"
          title="${escapeHtml(t('tipSpeakerNameInput'))}"
          onblur="updateSpeaker('${spkId}', this.value, null)"
          onkeydown="if(event.key==='Enter') this.blur()"
          class="flex-1 bg-transparent hover:bg-white/5 focus:bg-apple-card focus:outline-none px-2.5 py-1 rounded-md text-xs font-medium text-white" />

        ${s.voice_profile ? `<span class="text-[10.5px] px-1.5 py-0.5 rounded bg-apple-blue/15 text-apple-blue shrink-0"
          title="${escapeHtml(t('tipVoiceRecognised'))}">${escapeHtml(t('voiceRecognisedBadge'))}</span>` : ''}
        ${s.voice_suggest ? `<span class="flex items-center gap-1 shrink-0 text-[11px] px-1.5 py-0.5 rounded bg-apple-amber/15 text-apple-amber"
            title="${escapeHtml(t('tipVoiceSuggest'))}">${escapeHtml(t('voiceSuggest').replace('{name}', s.voice_suggest.name))}
            <button data-spk="${escapeHtml(spkId)}" data-name="${escapeHtml(s.voice_suggest.name)}" onclick="updateSpeaker(this.dataset.spk, this.dataset.name, null)"
              class="px-1.5 rounded bg-apple-amber/25 hover:bg-apple-amber/40 text-white">${escapeHtml(t('voiceSuggestYes'))}</button>
            <button data-spk="${escapeHtml(spkId)}" onclick="dismissVoiceSuggestion(this.dataset.spk)" class="px-1 hover:text-white">${escapeHtml(t('voiceSuggestNo'))}</button>
          </span>` : ''}
        <span class="text-[11px] text-apple-secondary font-mono">${turnCount} ${t('turns')}</span>

        <button onclick="previewSpeakerVoice(${s.sample_start || 0}, ${s.sample_end || 5})"
          title="${escapeHtml(t('tipPlayVoiceSample'))}"
          class="ui-btn ui-btn-subtle text-apple-label">
          ${SVG_ICONS.playMini}
          <span>${t('listenBtn')}</span>
        </button>
      </div>
    `;
  }).join('');
}

async function dismissVoiceSuggestion(speakerId) {
  const m = state.activeMeeting;
  const s = (m.speakers || {})[speakerId];
  if (!s) return;
  const res = await fetch(`/api/meetings/${m.id}/speakers`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speaker_id: speakerId, name: s.name, dismiss_suggestion: true }),
  });
  if (res.ok) { state.activeMeeting = await readJson(res); renderSpeakersPanel(); }
}

function previewSpeakerVoice(startSec, endSec) {
  const player = document.getElementById('media-player');
  if (!player) return;
  clearTimeout(state.voicePreviewTimer);
  player.currentTime = startSec;
  player.play();
  const durationMs = Math.max(2500, Math.min(6000, (endSec - startSec) * 1000));
  state.voicePreviewTimer = setTimeout(() => player.pause(), durationMs);
}

function filterBySpeaker(spkId) {
  state.speakerFilter = (state.speakerFilter === spkId) ? null : spkId;
  renderSpeakerPillsBar();
  renderTranscript();
}

async function updateSpeaker(speakerId, newName, newColor) {
  if (!state.activeMeeting) return;
  const current = state.activeMeeting.speakers[speakerId] || {};
  const nameToSave = newName !== null ? newName.trim() : current.name;
  const colorToSave = newColor !== null ? newColor : current.color;

  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/speakers`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      speaker_id: speakerId,
      name: nameToSave || speakerId,
      color: colorToSave
    })
  });
  state.activeMeeting = await readJson(res);
  renderSpeakerPillsBar();
  renderSpeakersPanel();
  renderTranscript();
}

// --- Transcript rendering with lazy blocks ---
// Long recordings have thousands of segments and tens of thousands of word spans. The transcript
// is split into blocks of TX_CHUNK segments; a block gets its DOM only while it is near the
// visible area (IntersectionObserver) and is replaced by an empty box of the same height when it
// scrolls far away. Short transcripts are rendered in one go.
const TX_CHUNK = 40;
const TX_EAGER_LIMIT = 150;
const tx = { items: [], query: '', speakers: {}, observer: null };

function renderTranscript() {
  const container = document.getElementById('transcript-inner');
  const m = state.activeMeeting;
  if (!m) return;

  const segments = m.segments || [];
  const speakers = m.speakers || {};
  const query = (document.getElementById('transcript-search').value || '').trim().toLowerCase();

  const filtered = segments.filter(seg => {
    if (state.speakerFilter && seg.speaker !== state.speakerFilter) return false;
    if (query) {
      const spkName = (speakers[seg.speaker]?.name || seg.speaker || '').toLowerCase();
      const tr = activeSegmentTranslation(seg);
      return seg.text.toLowerCase().includes(query) || spkName.includes(query)
        || (!!tr && (tr.text || '').toLowerCase().includes(query));
    }
    return true;
  });

  tx.observer?.disconnect();
  tx.observer = null;
  tx.items = filtered;
  tx.query = query;
  tx.speakers = speakers;

  if (filtered.length === 0) {
    container.innerHTML = `<div class="py-20 text-center text-xs text-apple-tertiary">
      ${segments.length === 0 ? '…' : '—'}
    </div>`;
    rebuildPlaybackIndex();
    return;
  }

  const chunkCount = Math.ceil(filtered.length / TX_CHUNK);
  const eager = filtered.length <= TX_EAGER_LIMIT;
  container.innerHTML = Array.from({ length: chunkCount }, (_, k) =>
    `<div class="tx-chunk space-y-1.5" data-chunk="${k}"${eager ? '' : ` style="height:${estimateChunkHeight(k)}px"`}></div>`
  ).join('');

  const chunkEls = container.querySelectorAll('.tx-chunk');
  if (eager) {
    chunkEls.forEach(renderTranscriptChunk);
  } else {
    tx.observer = new IntersectionObserver(onTranscriptChunkVisibility, {
      root: document.getElementById('transcript-container'),
      rootMargin: '1500px 0px',
    });
    chunkEls.forEach(el => tx.observer.observe(el));
  }
  rebuildPlaybackIndex();
}

function estimateChunkHeight(k) {
  let h = 0;
  for (const seg of tx.items.slice(k * TX_CHUNK, (k + 1) * TX_CHUNK)) {
    h += 52 + 22 * Math.ceil((seg.text || '').length / 95) + 6;
    if (state.txView === 'both') h += 20 * Math.ceil((seg.text || '').length / 95);
  }
  return h;
}

function onTranscriptChunkVisibility(entries) {
  for (const entry of entries) {
    const el = entry.target;
    if (entry.isIntersecting) {
      if (!el.dataset.rendered) renderTranscriptChunk(el);
    } else if (el.dataset.rendered && !el.contains(document.activeElement) && !el.querySelector('textarea')) {
      // Far away and not being edited: keep only an empty box of the measured height
      el.style.height = `${el.offsetHeight}px`;
      if (_playbackIndex.activeSeg && el.contains(_playbackIndex.activeSeg)) {
        _playbackIndex.activeSeg = null;
        _playbackIndex.activeWord = null;
        _playbackIndex.activeTrWord = null;
      }
      el.innerHTML = '';
      delete el.dataset.rendered;
    }
  }
}

function renderTranscriptChunk(el) {
  const k = Number(el.dataset.chunk);
  const start = k * TX_CHUNK;
  el.innerHTML = tx.items.slice(start, start + TX_CHUNK)
    .map((seg, j) => transcriptSegmentHtml(seg, start + j)).join('');
  el.style.height = '';
  el.dataset.rendered = '1';
}

function transcriptSegmentHtml(seg, idx) {
  const speakers = tx.speakers;
  const query = tx.query;
  const speakerOptionsHtml = (currentSpk) => Object.keys(speakers).map(k =>
    `<option value="${k}" ${k === currentSpk ? 'selected' : ''} class="bg-apple-card text-white">${escapeHtml(speakers[k].name)}</option>`
  ).join('');

  const spk = speakers[seg.speaker] || { name: seg.speaker || 'Speaker', color: '#0A84FF' };
  const words = seg.words || [];

  let bodyHtml = '';
  const tr = activeSegmentTranslation(seg);
  if (tr && state.txView === 'tr') {
    // Translation has no word timings: the whole segment is highlighted and seeks to its start
    bodyHtml = translationHtml(seg, tr, query, false);
  } else {
    if (words.length > 0 && !query) {
      bodyHtml = words.map(w =>
        `<span class="word-token cursor-pointer" data-start="${w.start}" data-end="${w.end}" onclick="seekPlayer(${w.start})">${escapeHtml(w.word)}</span>`
      ).join('');
    } else {
      bodyHtml = `<span class="cursor-pointer" onclick="seekPlayer(${seg.start})">${highlightSearch(escapeHtml(seg.text), query)}</span>`;
    }
    if (tr) bodyHtml += translationHtml(seg, tr, query, true);
  }

  const mergeBtnHtml = idx > 0 ? `
    <button onclick="mergeSegmentWithPrevious(${seg.id})"
      title="${escapeHtml(t('tipMergeSegment'))}"
      class="opacity-0 group-hover:opacity-100 inline-flex items-center gap-1 text-[11px] text-apple-tertiary hover:text-apple-blue transition">
      ${SVG_ICONS.mergeUp}
      <span>${t('mergeBtn')}</span>
    </button>
  ` : '';

  return `
    <div id="seg-${seg.id}" data-start="${seg.start}" data-end="${seg.end}"
      class="segment-row group px-3.5 py-2.5 flex items-baseline gap-4 hover:bg-white/[0.02]">
      <button onclick="seekPlayer(${seg.start})"
        title="${escapeHtml(t('tipSeekTimestamp'))}"
        class="text-[11px] font-mono text-apple-tertiary hover:text-apple-blue shrink-0 w-12 text-right transition">
        ${formatTime(seg.start)}
      </button>

      <div class="flex-1 min-w-0">
        <div class="flex items-center justify-between mb-0.5">
          <select onchange="changeSegmentSpeaker(${seg.id}, this.value)"
            title="${escapeHtml(t('tipChangeSegSpeaker'))}"
            style="color: ${spk.color}"
            class="bg-transparent text-xs font-semibold focus:outline-none cursor-pointer">
            ${speakerOptionsHtml(seg.speaker)}
          </select>
          <div class="flex items-center gap-2.5">
            ${mergeBtnHtml}
            <button onclick="openInlineSegmentEditor(${seg.id})"
              title="${escapeHtml(t('tipEditSegment'))}"
              class="opacity-0 group-hover:opacity-100 text-[11px] text-apple-tertiary hover:text-white transition">
              ${t('editBtn')}
            </button>
          </div>
        </div>
        <div id="seg-text-${seg.id}" class="text-[13.5px] text-apple-label/90 leading-relaxed">
          ${bodyHtml}
        </div>
      </div>
    </div>
  `;
}

// A translation has no word timings of its own. Each translated word gets an estimated time: the
// sentences of the translation are matched to the sentences of the original (whose words are
// timed), and inside a sentence the time runs in proportion to the length of the words. When the
// sentence counts differ, the whole phrase is spread the same way over the spoken time.
const _SENT_END = /[.!?…]["»”)]*$/;

function translationWordTimes(seg, words) {
  const start = Number(seg.start) || 0;
  const end = Math.max(start + 0.01, Number(seg.end) || start);
  const timed = (seg.words || []).filter(w => String(w.word || '').trim());
  const spread = (list, from, to) => {
    const lens = list.map(w => w.length + 1);
    const total = lens.reduce((a, b) => a + b, 0) || 1;
    let acc = 0;
    return lens.map(l => { const t0 = from + (to - from) * (acc / total); acc += l; return t0; });
  };
  const sentences = (list, text) => {
    const out = [[]];
    list.forEach((w, i) => {
      out[out.length - 1].push(i);
      if (_SENT_END.test(text(w).trim()) && i < list.length - 1) out.push([]);
    });
    return out;
  };
  if (timed.length) {
    const src = sentences(timed, w => String(w.word));
    const dst = sentences(words, w => w);
    if (src.length === dst.length && src.length > 1) {
      const times = new Array(words.length);
      dst.forEach((idx, k) => {
        const from = Number(timed[src[k][0]].start);
        const last = timed[src[k][src[k].length - 1]];
        spread(idx.map(i => words[i]), from, Math.max(from + 0.01, Number(last.end))).forEach((t0, j) => { times[idx[j]] = t0; });
      });
      return times;
    }
    return spread(words, Number(timed[0].start), Number(timed[timed.length - 1].end));
  }
  return spread(words, start, end);
}

function translationWordsHtml(seg, text) {
  const parts = text.split(/(\s+)/);
  const words = parts.filter(w => w.trim());
  const times = translationWordTimes(seg, words);
  let i = 0;
  return parts.map(w => {
    if (!w.trim()) return w;
    const t0 = Math.round(times[i++] * 100) / 100;
    return `<span class="tr-word" data-start="${t0}" onclick="event.stopPropagation(); seekPlayer(${t0})">${escapeHtml(w)}</span>`;
  }).join('');
}

function translationHtml(seg, tr, query, secondary) {
  const staleBadge = tr.stale
    ? ` <span class="text-[10px] text-apple-amber align-middle" title="${escapeHtml(t('tipTranslationStale'))}">${escapeHtml(t('translationStale'))}</span>`
    : '';
  // Without a search the words are separate spans, so playback can follow them (see updatePlaybackHighlight)
  const body = !tr.text
    ? `<span class="italic text-apple-tertiary">${escapeHtml(t('translationMissing'))}</span>`
    : query
      ? highlightSearch(escapeHtml(tr.text), query)
      : translationWordsHtml(seg, tr.text);
  const cls = secondary ? 'block mt-1 text-[13px] text-apple-secondary' : '';
  return `<span class="tr-text cursor-pointer ${cls} ${tr.stale ? 'opacity-70' : ''}" onclick="seekPlayer(${seg.start})">${body}${staleBadge}</span>`;
}

// Sorted segment list for O(log n) lookup of the playing segment; words are looked up inside it.
const _playbackIndex = { segments: [], activeSeg: null, activeWord: null, activeTrWord: null };

function rebuildPlaybackIndex() {
  _playbackIndex.segments = tx.items
    .map((seg, i) => ({ s: Number(seg.start), e: Number(seg.end), i }))
    .sort((a, b) => a.s - b.s);
  _playbackIndex.activeSeg = null;
  _playbackIndex.activeWord = null;
  _playbackIndex.activeTrWord = null;
}

function findActiveItem(items, t, pad, startOf = x => x.s, endOf = x => x.e) {
  let lo = 0, hi = items.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (startOf(items[mid]) <= t) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (found >= 0 && t <= endOf(items[found]) + pad) return found;
  return -1;
}

// DOM element of a transcript segment; renders its block first if asked to.
function transcriptSegmentElement(i, renderIfNeeded) {
  const seg = tx.items[i];
  if (!seg) return null;
  let el = document.getElementById(`seg-${seg.id}`);
  if (!el && renderIfNeeded) {
    const chunk = document.querySelector(`#transcript-inner .tx-chunk[data-chunk="${Math.floor(i / TX_CHUNK)}"]`);
    if (chunk) {
      renderTranscriptChunk(chunk);
      el = document.getElementById(`seg-${seg.id}`);
    }
  }
  return el;
}

function updatePlaybackHighlight(cur, followPlayback) {
  const hit = findActiveItem(_playbackIndex.segments, cur, 0);
  const segIdx = hit >= 0 ? _playbackIndex.segments[hit].i : -1;
  const segEl = segIdx >= 0 ? transcriptSegmentElement(segIdx, followPlayback) : null;
  if (segEl !== _playbackIndex.activeSeg) {
    _playbackIndex.activeSeg?.classList.remove('segment-active');
    segEl?.classList.add('segment-active');
    _playbackIndex.activeSeg = segEl;
  }

  let wordEl = null;
  if (segEl && !tx.query) {
    const words = tx.items[segIdx].words || [];
    const wi = findActiveItem(words, cur, 0.08, w => Number(w.start), w => Number(w.end));
    if (wi >= 0) wordEl = segEl.querySelectorAll('.word-token')[wi] || null;
  }
  if (wordEl !== _playbackIndex.activeWord) {
    _playbackIndex.activeWord?.classList.remove('word-active');
    wordEl?.classList.add('word-active');
    _playbackIndex.activeWord = wordEl;
  }

  // Translated words carry estimated times (see translationWordTimes): the last one started is lit
  let trWordEl = null;
  const trWords = segEl ? segEl.querySelectorAll('.tr-word') : [];
  for (const w of trWords) {
    if (Number(w.dataset.start) > cur + 0.05) break;
    trWordEl = w;
  }
  if (trWordEl !== _playbackIndex.activeTrWord) {
    _playbackIndex.activeTrWord?.classList.remove('word-active');
    trWordEl?.classList.add('word-active');
    _playbackIndex.activeTrWord = trWordEl;
  }
  return segEl;
}

// Highlights the whole query where it occurs as typed; otherwise each word of it (the global
// search matches words in any order, "борщ капуст" -> "борщ с капустой"). Works on escaped HTML
// and never touches entities such as &amp;.
function highlightSearch(safeText, query) {
  const q = (query || '').trim();
  if (!q) return safeText;
  const esc = (x) => escapeHtml(x).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const plain = safeText.replace(/&(#\d+|[a-z]+);/gi, (m) => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" }[m] || m));
  let terms;
  if (plain.toLowerCase().includes(q.toLowerCase())) {
    terms = [q];
  } else {
    const words = q.match(/[\p{L}\p{N}]+/gu) || [];
    terms = words.length > 1 ? words.filter(w => w.length > 1) : words;
  }
  if (!terms.length) return safeText;
  terms.sort((a, b) => b.length - a.length);
  const regex = new RegExp(`(${terms.map(esc).join('|')})`, 'giu');
  return safeText
    .split(/(&(?:#\d+|[a-z]+);)/gi)
    .map((part, i) => (i % 2 ? part : part.replace(regex, '<mark class="bg-apple-blue/40 text-white rounded px-0.5">$1</mark>')))
    .join('');
}

async function mergeSegmentWithPrevious(segId) {
  if (!state.activeMeeting) return;
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/segments/${segId}/merge`, {
    method: 'POST'
  });
  if (res.ok) {
    state.activeMeeting = await readJson(res);
    renderSpeakerPillsBar();
    renderSpeakersPanel();
    renderTranscript();
    showToast(state.uiLang === 'en' ? 'Segments merged' : 'Реплики объединены');
  }
}

// Filler words: one button that removes them or brings them back. After a cleanup (made
// automatically after transcription, or by hand) it shows what was cut: "−12 words · −8 s"
async function cleanActiveTranscriptFillers() {
  const m = state.activeMeeting;
  if (!m) return;
  if (m.has_segments_backup) {
    await restoreSegments(m.id);
    renderFillerButton();
    return;
  }
  const res = await fetch(`/api/meetings/${m.id}/clean-fillers`, { method: 'POST' });
  if (!res.ok) return;
  const data = await readJson(res);
  state.activeMeeting = data.meeting;
  renderTranscript();
  if (!data.removed_count) showToast(t('fillersNone'));
  renderFillerButton();
}

async function renderFillerButton() {
  const m = state.activeMeeting;
  const btn = document.getElementById('clean-fillers-btn');
  if (!btn || !m) return;
  const label = btn.querySelector('.tx-tool-label');
  let stats = { cleaned: false };
  if (m.has_segments_backup) stats = await fetch(`/api/meetings/${m.id}/fillers`).then(r => r.json()).catch(() => stats);
  if (state.activeMeeting !== m) return;
  if (stats.cleaned) {
    const parts = [stats.words ? `−${stats.words} ${t('fillersWordsShort')}` : '', stats.seconds ? `−${Math.round(stats.seconds)} ${t('unitSec')}` : ''].filter(Boolean);
    label.textContent = `${t('fillersRestore')}${parts.length ? ' · ' + parts.join(' · ') : ''}`;
    btn.title = t('tipFillersRestore');
    btn.classList.add('text-apple-blue');
  } else {
    label.textContent = t('cleanSpeechBtn');
    btn.title = t('tipCleanSpeech');
    btn.classList.remove('text-apple-blue');
  }
}

function openInlineSegmentEditor(segId) {
  const m = state.activeMeeting;
  if (!m) return;
  const seg = (m.segments || []).find(s => s.id === segId);
  if (!seg) return;

  const textBox = document.getElementById(`seg-text-${segId}`);
  textBox.innerHTML = `
    <textarea id="seg-edit-area-${segId}" rows="2"
      class="w-full bg-apple-sidebar border border-apple-blue rounded-lg p-2 text-xs text-white focus:outline-none mt-1">${escapeHtml(seg.text)}</textarea>
    <div class="flex justify-end gap-2 mt-1.5">
      <button onclick="renderTranscript()" title="${escapeHtml(t('tipCancelBtn'))}" class="ui-btn ui-btn-subtle">${t('cancel')}</button>
      <button onclick="saveSegmentEdit(${segId})" title="${escapeHtml(t('tipSaveSettingsBtn'))}" class="ui-btn ui-btn-primary">${t('save')}</button>
    </div>
  `;
}

async function saveSegmentEdit(segId) {
  const newText = document.getElementById(`seg-edit-area-${segId}`).value.trim();
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/segments/${segId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: newText })
  });
  state.activeMeeting = await readJson(res);
  renderTranscript();
}

async function changeSegmentSpeaker(segId, newSpeaker) {
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/segments/${segId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speaker: newSpeaker })
  });
  state.activeMeeting = await readJson(res);
  renderSpeakerPillsBar();
  renderSpeakersPanel();
  renderTranscript();
}
