// VerbaScribe UI logic. Depends on vendor/marked.min.js, vendor/purify.min.js and i18n.js.
const DONE_STATUSES = ['completed', 'failed', 'cancelled'];

let state = {
  uiLang: localStorage.getItem('whisper_ui_lang') || 'ru',
  meetings: [],
  activeMeeting: null,
  contextMeetingId: null,
  activeWorkspaceTab: 'transcript',
  activeSummaryTab: 'protocol',
  speakerFilter: null,
  videoMode: (() => { try { return localStorage.getItem('verbascribe_video_mode') === 'mini' ? 'mini' : 'expanded'; } catch (e) { return 'expanded'; } })(),
  hasVideo: false,
  audioTrack: 'orig',
  studioVoiceActive: false,
  globalSearchQuery: '',
  globalSearchResults: null,
  playbackSpeeds: [1.0, 1.25, 1.5, 1.75, 2.0],
  speedIndex: 0,
  system: null,
  dictionary: [],
  voicePreviewTimer: null,
  playerPeaks: [],
  playerHoverRatio: -1,
  volume: Number(localStorage.getItem('verbascribe_volume') ?? 100),
  muted: false,
  txView: (() => { try { return localStorage.getItem('verbascribe_tx_view') || 'orig'; } catch (e) { return 'orig'; } })(),
  txLang: null,
};

// Minimalist SVG Icon Helpers (uniform stroke style)
const SVG_ICONS = {
  download: `<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"/></svg>`,
  check: `<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5"/></svg>`,
  trash: `<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/></svg>`,
  playMini: `<svg class="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
  mergeUp: `<svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18"/></svg>`,
  closeX: `<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>`,
};

function t(key) {
  return (I18N[state.uiLang] && I18N[state.uiLang][key]) || I18N.ru[key] || key;
}

function setUILanguage(lang) {
  state.uiLang = lang;
  localStorage.setItem('whisper_ui_lang', lang);
  // The launcher's splash screen speaks the same language (see /api/ui/lang)
  fetch('/api/ui/lang', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang }) }).catch(() => {});

  document.getElementById('lang-btn-ru').className = lang === 'ru'
    ? 'px-2 h-6 text-[10.5px] font-medium rounded-[6px] apple-segment-active'
    : 'px-2 h-6 text-[10.5px] font-medium rounded-[6px] text-apple-secondary';
  document.getElementById('lang-btn-en').className = lang === 'en'
    ? 'px-2 h-6 text-[10.5px] font-medium rounded-[6px] apple-segment-active'
    : 'px-2 h-6 text-[10.5px] font-medium rounded-[6px] text-apple-secondary';

  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    el.setAttribute('placeholder', t(key));
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.getAttribute('data-i18n-title');
    el.setAttribute('title', t(key));
  });

  renderMeetingsSidebar();
  loadDictionary();
  if (state.system) {
    updateOllamaUI(state.system.ollama);
    if (state.system.models) updateWhisperModelsUI(state.system.models);
  }
  if (state.activeMeeting) {
    renderSpeakerPillsBar();
    renderSpeakersPanel();
    renderTranscript();
    renderSummaryPanel();
  }
  applyVideoLayout();
}

function showToast(msg, action) {
  const el = document.getElementById('toast');
  el.textContent = '';
  const span = document.createElement('span');
  span.textContent = msg;
  el.appendChild(span);
  if (action && action.label && typeof action.onClick === 'function') {
    const btn = document.createElement('button');
    btn.className = 'ml-3 text-apple-blue hover:underline font-semibold';
    btn.textContent = action.label;
    if (action.title) btn.title = action.title;
    btn.onclick = () => { el.classList.add('hidden'); action.onClick(); };
    el.appendChild(btn);
  }
  el.classList.remove('hidden');
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => el.classList.add('hidden'), action ? 7000 : 2600);
}

function formatTime(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) {
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }
  return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

// --- Modals & Menus ---
// Settings are split into tabs; the last one is remembered
const SETTINGS_TABS = ['general', 'record', 'recognition', 'analysis', 'dub'];

function selectSettingsTab(name) {
  if (!SETTINGS_TABS.includes(name)) name = 'general';
  document.querySelectorAll('#modal-settings [data-stab]').forEach(el => el.classList.toggle('hidden', el.dataset.stab !== name));
  SETTINGS_TABS.forEach(k => {
    const btn = document.getElementById(`stab-${k}`);
    if (btn) btn.className = `apple-segment-btn ${k === name ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  });
  try { localStorage.setItem('verbascribe_settings_tab', name); } catch (e) {}
}

function openModal(name, tab) {
  document.getElementById(`modal-${name}`).classList.remove('hidden');
  if (name === 'settings') {
    let last = null;
    try { last = localStorage.getItem('verbascribe_settings_tab'); } catch (e) {}
    selectSettingsTab(tab || last || 'general');
    loadVoiceProfiles();
    loadDubInfo();
    populateAudioInputDevices();
    refreshExportDirLabel();
    setTimeout(() => drawIdleSoundCloudBars('settings-waveform-canvas'), 30);
  }
}
// --- Voice profiles (Settings): names remembered from earlier recordings ---
async function loadVoiceProfiles() {
  const box = document.getElementById('voice-profiles-list');
  if (!box) return;
  try {
    const res = await fetch('/api/voice-profiles');
    renderVoiceProfiles(await res.json());
  } catch (e) {
    box.innerHTML = '';
  }
}

function renderVoiceProfiles(data) {
  const box = document.getElementById('voice-profiles-list');
  const list = (data && data.profiles) || [];
  if (!list.length) {
    box.innerHTML = `<div class="text-[11px] text-apple-tertiary">${escapeHtml(t('voiceProfilesNone'))}</div>`;
    return;
  }
  box.innerHTML = list.map(p => `
    <div class="flex items-center justify-between gap-2 text-xs">
      <span class="text-apple-label truncate">${escapeHtml(p.name)}
        <span class="text-apple-tertiary">· ${escapeHtml(t('voiceProfileSamples').replace('{n}', p.samples))}</span></span>
      <button onclick="deleteVoiceProfile(${Number(p.id)}, this.dataset.name)" data-name="${escapeHtml(p.name)}"
        class="ui-btn ui-btn-subtle h-6 px-2 text-[11px] text-apple-red" title="${escapeHtml(t('tipVoiceProfileDelete'))}">${escapeHtml(t('voiceProfileDelete'))}</button>
    </div>`).join('');
}

async function deleteVoiceProfile(id, name) {
  if (!confirm(t('voiceProfileDeleteConfirm').replace('{name}', name || ''))) return;
  const res = await fetch(`/api/voice-profiles/${id}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  renderVoiceProfiles(data);
}

function closeModal(name) {
  if (name === 'settings') {
    stopSettingsAudioTest();
  }
  document.getElementById(`modal-${name}`).classList.add('hidden');
}
function closeModalOnBackdrop(e, name) {
  if (e.target === e.currentTarget) closeModal(name);
}

function switchWorkspaceTab(tab) {
  state.activeWorkspaceTab = tab;
  ['transcript', 'summary', 'speakers'].forEach(k => {
    document.getElementById(`wview-${k}`).classList.toggle('hidden', k !== tab);
    const btn = document.getElementById(`wtab-${k}`);
    btn.className = (k === tab)
      ? 'apple-segment-btn apple-segment-active'
      : 'apple-segment-btn text-apple-secondary hover:text-white';
  });
}

function toggleExportMenu(e) {
  e.stopPropagation();
  hideContextMenu();
  document.getElementById('export-menu').classList.toggle('hidden');
}

// --- Right-Click Context Menu on Sidebar Recording Items ---
function openRecordingContextMenu(e, meetingId) {
  e.preventDefault();
  e.stopPropagation();
  document.getElementById('export-menu')?.classList.add('hidden');

  state.contextMeetingId = meetingId;
  const m = state.meetings.find(x => x.id === meetingId);
  if (!m) return;

  document.getElementById('ctx-title-header').textContent = m.title;
  document.getElementById('ctx-cancel-item').classList.toggle('hidden', DONE_STATUSES.includes(m.status));
  const hasBackup = state.activeMeeting && state.activeMeeting.id === m.id && state.activeMeeting.has_segments_backup;
  document.getElementById('ctx-restore-item').classList.toggle('hidden', !hasBackup);
  const menu = document.getElementById('recording-context-menu');
  menu.classList.remove('hidden');

  const x = Math.min(e.clientX, window.innerWidth - 235);
  const y = Math.min(e.clientY, window.innerHeight - 265);
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
}

function hideContextMenu() {
  document.getElementById('recording-context-menu')?.classList.add('hidden');
}

document.addEventListener('click', () => {
  hideContextMenu();
  document.getElementById('export-menu')?.classList.add('hidden');
  hideTranslateMenu();
  hideTxViewMenu();
});

function toggleTranslateMenu(e) {
  e.stopPropagation();
  hideContextMenu();
  document.getElementById('export-menu')?.classList.add('hidden');
  document.getElementById('translate-menu').classList.toggle('hidden');
}

function hideTranslateMenu() {
  document.getElementById('translate-menu')?.classList.add('hidden');
}

async function ctxRenameRecording() {
  hideContextMenu();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  if (!m) return;
  const newTitle = prompt(state.uiLang === 'en' ? 'New recording title:' : 'Новое название записи:', m.title);
  if (!newTitle || !newTitle.trim() || newTitle.trim() === m.title) return;

  await fetch(`/api/meetings/${m.id}/title`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle.trim() })
  });
  await loadMeetingsList();
  if (state.activeMeeting && state.activeMeeting.id === m.id) {
    state.activeMeeting.title = newTitle.trim();
    document.getElementById('meeting-title-input').value = newTitle.trim();
  }
}

function ctxOpenReconfigureModal() {
  hideContextMenu();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  if (!m) return;

  document.getElementById('reconfig-recording-name').textContent = m.title;
  document.getElementById('reconfig-model').value = m.model_name || 'large-v3-turbo';
  document.getElementById('reconfig-language').value = m.language || 'auto';
  document.getElementById('reconfig-speakers').value = String(m.num_speakers_hint || 0);
  openModal('reconfigure');
}

async function submitReconfigure() {
  const id = state.contextMeetingId;
  if (!id) return;
  closeModal('reconfigure');

  await fetch(`/api/meetings/${id}/reconfigure`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model_name: document.getElementById('reconfig-model').value,
      language: document.getElementById('reconfig-language').value,
      num_speakers_hint: Number(document.getElementById('reconfig-speakers').value || 0),
    })
  });
  await loadMeetingsList();
  await selectMeeting(id, false);
  showToast(state.uiLang === 'en' ? 'Re-processing started' : 'Запущена повторная обработка');
}

async function ctxCancelProcessing() {
  hideContextMenu();
  if (state.contextMeetingId) await cancelProcessing(state.contextMeetingId);
}

async function ctxRestoreSegments() {
  hideContextMenu();
  if (state.contextMeetingId) await restoreSegments(state.contextMeetingId);
}

async function restoreSegments(id) {
  const res = await fetch(`/api/meetings/${id}/restore-segments`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  if (state.activeMeeting && state.activeMeeting.id === id) {
    state.activeMeeting = data;
    renderTranscript();
    renderSpeakersPanel();
  }
  showToast(state.uiLang === 'en' ? 'Original text restored' : 'Исходный текст восстановлен');
}

async function ctxRevealInExplorer() {
  hideContextMenu();
  if (!state.contextMeetingId) return;
  await fetch(`/api/meetings/${state.contextMeetingId}/reveal`, { method: 'POST' });
}

async function ctxExport(fmt) {
  hideContextMenu();
  if (!state.contextMeetingId) return;
  await exportMeetingById(state.contextMeetingId, fmt);
}

async function ctxDeleteRecording() {
  hideContextMenu();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  if (!m) return;
  if (!confirm(t('confirmDelete').replace('{title}', m.title))) return;

  const delRes = await fetch(`/api/meetings/${m.id}`, { method: 'DELETE' });
  if (!delRes.ok) {
    const data = await delRes.json().catch(() => ({}));
    showToast(data.detail || 'Error');
    await loadMeetingsList();
    return;
  }
  if (state.activeMeeting && state.activeMeeting.id === m.id) {
    state.activeMeeting = null;
    document.getElementById('workspace-content').classList.add('hidden');
    document.getElementById('workspace-empty').classList.remove('hidden');
  }
  await loadMeetingsList();
  showToast(state.uiLang === 'en' ? 'Recording deleted' : 'Запись удалена');
}

// --- System & Settings ---
// --- FFmpeg status / optional download ---
function renderFfmpegStatus(st) {
  const row = document.getElementById('ffmpeg-row');
  if (!row || !st) return;
  row.classList.remove('hidden');
  const textEl = document.getElementById('ffmpeg-status-text');
  const btn = document.getElementById('ffmpeg-download-btn');
  const dl = st.download || {};
  if (dl.active) {
    textEl.textContent = `${t('ffmpegDownloading')}: ${dl.progress}% (${dl.downloaded_mb} MB)`;
  } else if (st.available) {
    textEl.textContent = `${st.source === 'downloaded' ? t('ffmpegDownloaded') : t('ffmpegSystem')} · ${st.ffmpeg}`;
  } else {
    textEl.textContent = dl.error ? `${t('ffmpegMissing')} · ${dl.error}` : t('ffmpegMissing');
  }
  textEl.title = textEl.textContent;
  btn.classList.toggle('hidden', st.available || dl.active || !st.can_download);
  const info = st.download_info || {};
  btn.title = t('tipFfmpegDownload').replace('{version}', info.version || '').replace('{size}', info.size_mb || '');
}

async function installFfmpeg() {
  let st = await (await fetch('/api/ffmpeg/install', { method: 'POST' })).json();
  renderFfmpegStatus(st);
  while (st.download && st.download.active) {
    await new Promise(r => setTimeout(r, 1000));
    st = await (await fetch('/api/ffmpeg/status')).json();
    renderFfmpegStatus(st);
  }
}

async function loadSystemInfo() {
  try {
    const res = await fetch('/api/system');
    const data = await res.json();
    state.system = data;

    const hw = data.hardware;
    document.getElementById('hw-text').textContent = hw.cuda_available
      ? hw.device_name.replace('NVIDIA GeForce ', '')
      : 'CPU';
    document.getElementById('hw-dot').className = hw.cuda_available
      ? 'w-1.5 h-1.5 rounded-full bg-apple-green shrink-0'
      : 'w-1.5 h-1.5 rounded-full bg-apple-amber shrink-0';

    renderFfmpegStatus(data.ffmpeg);
    if (data.paths) {
      const dirEl = document.getElementById('settings-data-dir');
      dirEl.textContent = `${t('dataFolder')}: ${data.paths.data}`;
      dirEl.title = `${t('dataFolder')}: ${data.paths.data}
${t('modelsFolder')}: ${data.paths.models}`;
      const conflict = data.paths.legacy_conflict;
      document.getElementById('legacy-conflict-banner').classList.toggle('hidden', !conflict);
      if (conflict) {
        document.getElementById('legacy-conflict-text').textContent = t('legacyConflictText')
          .replace('{legacy}', conflict.legacy)
          .replace('{count}', conflict.legacy_recordings)
          .replace('{current}', conflict.current);
      }
    }

    document.getElementById('settings-hw-name').textContent = hw.cuda_available
      ? `${hw.device_name} · ${hw.vram_total_gb} GB VRAM`
      : hw.device_name;

    const s = data.settings;
    document.getElementById('set-whisper-model').value = s.whisper_model || 'large-v3-turbo';
    document.getElementById('upload-model').value = s.whisper_model || 'large-v3-turbo';
    document.getElementById('set-device').value = s.device || 'cuda';
    document.getElementById('set-compute-type').value = s.compute_type || 'float16';
    document.getElementById('set-diarization').checked = s.diarization_enabled !== 'false';
    document.getElementById('set-voice-profiles').checked = s.voice_profiles !== 'false';
    document.getElementById('set-vad').checked = s.vad_filter !== 'false';
    if (document.getElementById('set-audio-enhance')) {
      document.getElementById('set-audio-enhance').checked = s.audio_enhance !== 'false';
    }
    if (document.getElementById('set-clean-fillers')) {
      document.getElementById('set-clean-fillers').checked = s.clean_fillers !== 'false';
    }
    document.getElementById('set-ollama-model').value = s.ollama_model || 'qwen2.5:7b';
    document.getElementById('set-auto-summary').checked = s.auto_summary !== 'false';
    fillDubSettings(s);

    const srcMode = s.record_source_mode || 'both';
    if (document.getElementById('set-record-source')) {
      document.getElementById('set-record-source').value = srcMode;
    }
    if (document.getElementById('record-source-select')) {
      document.getElementById('record-source-select').value = srcMode;
    }

    updateOllamaUI(data.ollama);
    if (data.models) {
      updateWhisperModelsUI(data.models);
      if (data.models.needs_first_run_setup && !window._firstRunShown) {
        window._firstRunShown = true;
        openModal('settings', 'recognition');
      }
    }
    syncSidebarDownloadWidget();

    // Automatically attach background pollers if a download is already running on the backend
    if (data.models && data.models.setup_state && data.models.setup_state.active && !window._whisperPollTimer) {
      startWhisperPollingLoop(data.models.setup_state.model_id || 'Whisper');
    }
    if (data.ollama && data.ollama.pull_state && data.ollama.pull_state.active && !window._ollamaPollTimer) {
      startOllamaPollingLoop(data.ollama.pull_state.model || 'LLM');
    }
  } catch (e) {
    console.error(e);
  }
}

// --- Models in VRAM: chips under the GPU badge (unload on click, or load in advance) ---
const vram = { timer: null, busy: false, data: null };
const VRAM_POLL_MS = 5000;

function scheduleVramRefresh(delay = VRAM_POLL_MS) {
  clearTimeout(vram.timer);
  vram.timer = setTimeout(loadVram, delay);
}

async function loadVram() {
  try {
    const res = await fetch('/api/vram');
    if (res.ok) renderVram(await res.json());
  } catch (e) {
    // the server may be restarting; try again later
  } finally {
    scheduleVramRefresh(document.hidden ? VRAM_POLL_MS * 4 : VRAM_POLL_MS);
  }
}

function vramChipLabel(c) {
  if (c.kind === 'dub') return `${t('vramChipDub')}${c.count > 1 ? ' ×' + c.count : ''}`;
  if (c.kind === 'speakers') return t('vramChipSpeakers');
  if (c.kind === 'ollama') return `${c.label || c.name}${c.size_gb ? ' · ' + c.size_gb + ' ' + t('unitGB') : ''}`;
  return c.name;
}

function vramChipTitle(c) {
  if (c.kind === 'dub') return t('tipVramDub');
  if (c.loading) return t('tipVramLoading').replace('{name}', vramChipLabel(c));
  if (c.loaded) return c.can_unload ? t('tipVramUnload').replace('{name}', vramChipLabel(c)) : t('tipVramInUse');
  return c.can_preload ? t('tipVramPreload').replace('{name}', vramChipLabel(c)) : t('tipVramNotLoaded');
}

function renderVram(data) {
  vram.data = data;
  const row = document.getElementById('vram-row');
  const gpu = data.gpu;
  row.classList.toggle('hidden', !gpu);
  if (!gpu) return;
  document.getElementById('vram-text').textContent = `${gpu.used_gb} / ${gpu.total_gb} ${t('unitGB')}`;
  document.getElementById('vram-bar').style.width = `${Math.min(100, (100 * gpu.used_gb) / Math.max(1, gpu.total_gb))}%`;
  row.firstElementChild.title = t('tipVramUsage').replace('{free}', gpu.free_gb);

  const chips = (data.chips || []).filter(c => c.loaded || c.loading || c.can_preload);
  const html = chips.map((c, i) => {
    const actionable = vram.busy || c.loading ? false : (c.loaded ? c.can_unload : c.can_preload);
    const cls = c.loading
      ? 'bg-apple-blue/10 text-apple-blue border-apple-blue/40 animate-pulse'
      : c.loaded
      ? (c.kind === 'dub' ? 'bg-apple-amber/15 text-apple-amber border-apple-amber/30' : 'bg-apple-green/15 text-apple-green border-apple-green/30')
      : 'bg-transparent text-apple-tertiary border-white/15 border-dashed';
    const icon = c.loaded
      ? (actionable ? '<span class="opacity-60 group-hover:opacity-100">×</span>' : '')
      : (actionable ? '<span class="opacity-70 group-hover:opacity-100">↑</span>' : '');
    return `<button data-i="${i}" onclick="vramChipClick(this.dataset.i)" ${actionable || c.kind === 'dub' ? '' : 'disabled'}
      title="${escapeHtml(vramChipTitle(c))}"
      class="group max-w-full inline-flex items-center gap-1 px-1.5 h-5 rounded-full border text-[10px] font-medium transition ${cls} ${actionable ? 'hover:bg-white/10' : 'cursor-default'}">
      <span class="w-1.5 h-1.5 rounded-full shrink-0 ${c.loaded || c.loading ? 'bg-current' : 'border border-current'}"></span>
      <span class="truncate">${escapeHtml(vramChipLabel(c))}</span>${icon}</button>`;
  }).join('');
  // Unchanged chips are left alone, so a refresh never swallows a click
  const box = document.getElementById('vram-chips');
  if (html !== vram.html) { box.innerHTML = html; vram.html = html; }
  vram.chips = chips;
}

async function vramChipClick(index) {
  const c = (vram.chips || [])[Number(index)];
  if (!c || vram.busy) return;
  if (c.kind === 'dub') { showToast(t('tipVramDub')); return; }
  const action = c.loaded ? 'unload' : 'preload';
  if (action === 'unload' && !c.can_unload) return;
  if (action === 'preload' && !c.can_preload) return;
  vram.busy = true;
  renderVram(vram.data);
  showToast(t(action === 'unload' ? 'vramUnloading' : 'vramPreloading').replace('{name}', vramChipLabel(c)));
  try {
    const res = await fetch(`/api/vram/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: c.kind, name: c.name || '' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) showToast(data.detail || 'Error');
    else renderVram(data);
  } catch (e) {
    console.error(e);
  } finally {
    vram.busy = false;
    // Whisper is loaded by the job worker in the background: look again shortly
    scheduleVramRefresh(action === 'preload' ? 1500 : 300);
  }
}

const MODEL_DISPLAY_NAMES = {
  'large-v3-turbo': 'Whisper Turbo',
  'large-v3': 'Whisper Pro',
  'medium': 'Whisper Medium',
  'small': 'Whisper Light',
  'qwen2.5:7b': 'Qwen 7B Standard',
  'qwen2.5:14b': 'Qwen 14B Pro',
  'mistral:7b': 'Mistral 7B',
  'qwen2.5:1.5b': 'Qwen Lite',
      };

function formatModelName(modelId) {
  const raw = String(modelId || '').trim();
  if (!raw) return 'AI';
  const lower = raw.toLowerCase();
  if (MODEL_DISPLAY_NAMES[lower]) return MODEL_DISPLAY_NAMES[lower];
  for (const [k, v] of Object.entries(MODEL_DISPLAY_NAMES)) {
    if (lower.startsWith(k)) return v;
  }
  const parts = raw.split(':');
  const family = parts[0].replace(/[-_]/g, ' ').replace(/[\d.]+$/, '').trim() || parts[0];
  const familyTitle = family.charAt(0).toUpperCase() + family.slice(1);
  if (parts.length > 1 && parts[1].toLowerCase() !== 'latest') {
    return `${familyTitle} ${parts[1].toUpperCase().replace(/\./g, ' ')}`;
  }
  return familyTitle;
}

function toggleSidebarDownloadLogs() {
  const box = document.getElementById('sidebar-dl-logs');
  if (box) box.classList.toggle('hidden');
}

function syncSidebarDownloadWidget() {
  const widget = document.getElementById('sidebar-download-widget');
  if (!widget) return;

  const wState = state.whisperPullState;
  const oState = state.ollamaPullState;

  let activeTitle = '';
  let pct = 0;
  let statusText = '';
  let logs = [];

  if (wState && wState.active) {
    activeTitle = formatModelName(wState.model_id || 'Whisper');
    pct = wState.progress || 1;
    statusText = wState.status || '';
    logs = wState.logs || [];
  } else if (oState && oState.active) {
    activeTitle = formatModelName(oState.model || 'Ollama');
    pct = oState.progress || 1;
    statusText = oState.status || '';
    logs = oState.logs || [];
  } else {
    widget.classList.add('hidden');
    return;
  }

  widget.classList.remove('hidden');
  const dlTitleEl = document.getElementById('sidebar-dl-title');
  const dlStatusEl = document.getElementById('sidebar-dl-status');
  dlTitleEl.textContent = activeTitle;
  dlTitleEl.title = activeTitle;
  document.getElementById('sidebar-dl-pct').textContent = `${pct}%`;
  document.getElementById('sidebar-dl-bar').style.width = `${Math.max(2, pct)}%`;
  dlStatusEl.textContent = statusText;
  dlStatusEl.title = statusText;

  const logsEl = document.getElementById('sidebar-dl-logs');
  if (logsEl && logs.length > 0) {
    logsEl.innerHTML = logs.map(l => `<div>${escapeHtml(l)}</div>`).join('');
    logsEl.scrollTop = logsEl.scrollHeight;
  }
}

function updateWhisperModelsUI(models) {
  if (!models) return;
  const st = models.setup_state || {};
  state.whisperPullState = st;

  const banner = document.getElementById('first-run-banner');
  if (banner) {
    banner.classList.toggle('hidden', !models.needs_first_run_setup);
  }

  const diarPill = document.getElementById('diarizer-status-pill');
  const diarInstalled = models.diarizer_installed || (models.diarizer && models.diarizer.installed);
  if (diarPill) {
    if (diarInstalled) {
      diarPill.textContent = state.uiLang === 'en' ? 'Speaker model ready' : 'Модель участников готова';
      diarPill.className = 'text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-apple-green/15 text-apple-green shrink-0';
    } else {
      diarPill.textContent = state.uiLang === 'en' ? 'Downloads with Whisper' : 'Скачается вместе с Whisper';
      diarPill.className = 'text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-white/5 text-apple-secondary shrink-0';
    }
  }

  const activeModel = document.getElementById('set-whisper-model').value || models.active_whisper || 'large-v3-turbo';
  const catalogBox = document.getElementById('whisper-catalog-list');
  const whisperList = models.whisper_catalog || models.whisper || [];

  if (catalogBox && whisperList.length > 0) {
    catalogBox.innerHTML = whisperList.map(item => {
      const isSelected = item.id === activeModel;
      const desc = state.uiLang === 'en' ? item.desc_en : item.desc_ru;
      const isDownloadingThis = st.active && st.model_id === item.id;
      const sizeLabel = item.size || (item.size_mb >= 1000 ? (item.size_mb / 1024).toFixed(1) + ' GB' : (item.size_mb || '') + ' MB');
      const badgeLabel = item.badge || sizeLabel;

      let actionHtml = '';
      if (isDownloadingThis) {
        actionHtml = `<span class="ui-btn bg-apple-blue/20 text-apple-blue font-mono animate-pulse">${st.progress || 1}%</span>`;
      } else if (item.installed && isSelected) {
        actionHtml = `
          <div class="flex items-center gap-1.5">
            <span class="ui-btn bg-apple-green/20 text-apple-green">${SVG_ICONS.check}<span>${state.uiLang === 'en' ? 'Active' : 'Активна'}</span></span>
            <button data-id="${escapeHtml(item.id)}" onclick="deleteWhisperModel(this.dataset.id)" title="${escapeHtml(t('tipDeleteModel'))}"
              class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button>
          </div>
        `;
      } else if (item.installed) {
        actionHtml = `
          <div class="flex items-center gap-1.5">
            <button data-id="${escapeHtml(item.id)}" onclick="selectActiveWhisperModel(this.dataset.id)" title="${escapeHtml(t('tipSelectModel'))}"
              class="ui-btn ui-btn-subtle text-white hover:bg-apple-blue">
              ${state.uiLang === 'en' ? 'Select' : 'Выбрать'}
            </button>
            <button data-id="${escapeHtml(item.id)}" onclick="deleteWhisperModel(this.dataset.id)" title="${escapeHtml(t('tipDeleteModel'))}"
              class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button>
          </div>
        `;
      } else {
        actionHtml = `
          <button data-id="${escapeHtml(item.id)}" onclick="startWhisperModelDownload(this.dataset.id)" title="${escapeHtml(t('tipDownloadModel'))}"
            class="ui-btn ui-btn-primary">
            ${SVG_ICONS.download}
            <span>${state.uiLang === 'en' ? 'Download' : 'Скачать'} (${escapeHtml(sizeLabel)})</span>
          </button>
        `;
      }

      return `
        <div class="p-3 rounded-xl border transition flex items-center justify-between gap-3 ${
          isSelected ? 'bg-apple-blue/10 border-apple-blue/50' : 'bg-apple-sidebar border-white/10'
        }">
          <div class="min-w-0 flex-1">
            <div class="flex items-center flex-wrap gap-2">
              <span class="text-xs font-semibold text-white">${escapeHtml(formatModelName(item.id))}</span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">${escapeHtml(badgeLabel)}</span>
              <span class="text-[10px] text-apple-tertiary">${escapeHtml(item.vram)}</span>
            </div>
            <p class="text-[11.5px] text-apple-secondary mt-1 leading-snug">${escapeHtml(desc)}</p>
          </div>
          <div class="shrink-0">${actionHtml}</div>
        </div>
      `;
    }).join('');
  }

  const dlPanel = document.getElementById('whisper-download-panel');
  if (dlPanel && (st.active || (st.logs && st.logs.length > 0))) {
    dlPanel.classList.remove('hidden');
    const wPullStatusEl = document.getElementById('whisper-pull-status');
    wPullStatusEl.textContent = st.status || '';
    wPullStatusEl.title = st.status || '';
    document.getElementById('whisper-pull-pct').textContent = `${st.progress || 0}%`;
    document.getElementById('whisper-pull-bar').style.width = `${st.progress || 0}%`;

    const logsBox = document.getElementById('whisper-pull-logs');
    if (logsBox && st.logs) {
      logsBox.innerHTML = st.logs.map(l => `<div>${escapeHtml(l)}</div>`).join('');
      logsBox.scrollTop = logsBox.scrollHeight;
    }
  }
  syncSidebarDownloadWidget();
}

async function selectActiveWhisperModel(modelId) {
  document.getElementById('set-whisper-model').value = modelId;
  document.getElementById('upload-model').value = modelId;
  await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: { whisper_model: modelId } })
  });
  await loadSystemInfo();
  const pretty = formatModelName(modelId);
  showToast(state.uiLang === 'en' ? `Speech model: ${pretty}` : `Модель речи: ${pretty}`);
}

async function deleteWhisperModel(modelId) {
  await fetch(`/api/models/${encodeURIComponent(modelId)}`, { method: 'DELETE' });
  await loadSystemInfo();
  const pretty = formatModelName(modelId);
  showToast(state.uiLang === 'en' ? `Deleted ${pretty} from disk` : `Модель ${pretty} удалена с диска`);
}

function startWhisperPollingLoop(modelId) {
  clearInterval(window._whisperPollTimer);
  window._whisperPollTimer = setInterval(async () => {
    try {
      const res = await fetch('/api/models/status');
      const data = await res.json();
      updateWhisperModelsUI(data);
      if (!data.setup_state || !data.setup_state.active) {
        clearInterval(window._whisperPollTimer);
        window._whisperPollTimer = null;
        await loadSystemInfo();
        if (data.setup_state && data.setup_state.progress === 100) {
          const pretty = formatModelName(modelId);
          showToast(state.uiLang === 'en' ? `${pretty} is ready!` : `Модель ${pretty} установлена и готова к работе!`);
        }
      }
    } catch (e) {}
  }, 800);
}

async function startWhisperModelDownload(modelId) {
  document.getElementById('set-whisper-model').value = modelId;
  document.getElementById('upload-model').value = modelId;
  const pretty = formatModelName(modelId);

  state.whisperPullState = {
    active: true,
    model_id: modelId,
    progress: 1,
    status: state.uiLang === 'en' ? `Starting download of ${pretty}` : `Запуск скачивания ${pretty}`,
    logs: []
  };
  syncSidebarDownloadWidget();

  const dlPanel = document.getElementById('whisper-download-panel');
  if (dlPanel) dlPanel.classList.remove('hidden');
  document.getElementById('whisper-pull-status').textContent = state.whisperPullState.status;
  document.getElementById('whisper-pull-pct').textContent = '1%';
  document.getElementById('whisper-pull-bar').style.width = '2%';

  await fetch('/api/models/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      whisper_model: modelId,
      install_diarizer: true
    })
  });

  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: { whisper_model: modelId } })
  });

  startWhisperPollingLoop(modelId);
}

function updateOllamaUI(ollama) {
  if (!ollama) return;
  const ps = ollama.pull_state || {};
  state.ollamaPullState = ps;

  const pill = document.getElementById('settings-ollama-pill');
  const catalogBox = document.getElementById('ollama-catalog-list');
  const activeModel = document.getElementById('set-ollama-model').value || ollama.active_model || 'qwen2.5:7b';

  if (ollama.running) {
    pill.textContent = state.uiLang === 'en' ? 'Ollama Active' : 'Ollama запущена';
    pill.className = 'text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-apple-green/15 text-apple-green shrink-0';
  } else {
    pill.textContent = ollama.installed
      ? (state.uiLang === 'en' ? 'Ollama not running' : 'Ollama не запущена')
      : (state.uiLang === 'en' ? 'Ollama not installed' : 'Ollama не установлена');
    pill.className = 'text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-apple-amber/15 text-apple-amber shrink-0';
  }

  const summarySelect = document.getElementById('summary-llm-select');
  if (summarySelect && ollama.catalog) {
    summarySelect.innerHTML = ollama.catalog.map(item => {
      const pretty = formatModelName(item.id);
      const mark = item.installed ? (state.uiLang === 'en' ? '● ' : '● ') : '';
      return `<option value="${escapeHtml(item.id)}" ${item.id === activeModel ? 'selected' : ''}>${mark}${escapeHtml(pretty)}</option>`;
    }).join('');
  }

  if (catalogBox && ollama.catalog) {
    catalogBox.innerHTML = ollama.catalog.map(item => {
      const isSelected = item.id === activeModel;
      const desc = state.uiLang === 'en' ? item.desc_en : item.desc_ru;
      const isPullingThis = ps && ps.active && ps.model === item.id;
      const pretty = formatModelName(item.id);
      const badgeLabel = item.badge || item.size || '';

      let actionHtml = '';
      if (isPullingThis) {
        actionHtml = `<span class="ui-btn bg-apple-blue/20 text-apple-blue font-mono animate-pulse">${ps.progress || 1}%</span>`;
      } else if (item.installed && isSelected) {
        actionHtml = `<span class="ui-btn bg-apple-green/20 text-apple-green">${SVG_ICONS.check}<span>${state.uiLang === 'en' ? 'Active' : 'Активна'}</span></span>`;
      } else if (item.installed) {
        actionHtml = `
          <button data-id="${escapeHtml(item.id)}" onclick="selectActiveOllamaModel(this.dataset.id)" title="${escapeHtml(t('tipSelectModel'))}"
            class="ui-btn ui-btn-subtle text-white hover:bg-apple-blue">
            ${state.uiLang === 'en' ? 'Select' : 'Выбрать'}
          </button>
        `;
      } else {
        actionHtml = `
          <button data-id="${escapeHtml(item.id)}" onclick="pullOllamaModel(this.dataset.id)" title="${escapeHtml(t('tipDownloadModel'))}"
            class="ui-btn ui-btn-primary">
            ${SVG_ICONS.download}
            <span>${state.uiLang === 'en' ? 'Download' : 'Скачать'} (${escapeHtml(item.size)})</span>
          </button>
        `;
      }

      return `
        <div class="p-3 rounded-xl border transition flex items-center justify-between gap-3 ${
          isSelected ? 'bg-apple-blue/10 border-apple-blue/50' : 'bg-apple-sidebar border-white/10'
        }">
          <div class="min-w-0 flex-1">
            <div class="flex items-center flex-wrap gap-2">
              <span class="text-xs font-semibold text-white">${escapeHtml(pretty)}</span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">${escapeHtml(badgeLabel)}</span>
              <span class="text-[10px] text-apple-tertiary">${escapeHtml(item.vram)}</span>
            </div>
            <p class="text-[11.5px] text-apple-secondary mt-1 leading-snug">${escapeHtml(desc)}</p>
          </div>
          <div class="shrink-0">${actionHtml}</div>
        </div>
      `;
    }).join('');
  }

  const dlPanel = document.getElementById('ollama-download-panel');
  if (dlPanel && ps && (ps.active || (ps.logs && ps.logs.length > 0))) {
    dlPanel.classList.remove('hidden');
    const oPullStatusEl = document.getElementById('ollama-pull-status');
    oPullStatusEl.textContent = ps.status || '';
    oPullStatusEl.title = ps.status || '';
    document.getElementById('ollama-pull-pct').textContent = `${ps.progress || 0}%`;
    document.getElementById('ollama-pull-bar').style.width = `${ps.progress || 0}%`;

    const logsBox = document.getElementById('ollama-pull-logs');
    if (logsBox && ps.logs) {
      logsBox.innerHTML = ps.logs.map(l => `<div>${escapeHtml(l)}</div>`).join('');
      logsBox.scrollTop = logsBox.scrollHeight;
    }
  }
  syncSidebarDownloadWidget();
}

async function selectActiveOllamaModel(modelId) {
  document.getElementById('set-ollama-model').value = modelId;
  const summarySelect = document.getElementById('summary-llm-select');
  if (summarySelect) summarySelect.value = modelId;

  await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: { ollama_model: modelId } })
  });
  await loadSystemInfo();
  const pretty = formatModelName(modelId);
  showToast(state.uiLang === 'en' ? `Selected LLM: ${pretty}` : `Выбрана модель: ${pretty}`);
}

async function saveAllSettings() {
  const payload = {
    settings: {
      whisper_model: document.getElementById('set-whisper-model').value,
      device: document.getElementById('set-device').value,
      compute_type: document.getElementById('set-compute-type').value,
      diarization_enabled: String(document.getElementById('set-diarization').checked),
      voice_profiles: String(document.getElementById('set-voice-profiles').checked),
      vad_filter: String(document.getElementById('set-vad').checked),
      audio_enhance: String(document.getElementById('set-audio-enhance')?.checked !== false),
      clean_fillers: String(document.getElementById('set-clean-fillers')?.checked !== false),
      ollama_model: document.getElementById('set-ollama-model').value.trim(),
      auto_summary: String(document.getElementById('set-auto-summary').checked),
      record_source_mode: document.getElementById('set-record-source')?.value || 'both',
      record_mic_device: document.getElementById('set-record-mic')?.value || '',
      ...dubSettingsPayload(),
    }
  };
  // Close the modal immediately so the user is never blocked by background downloads
  closeModal('settings');
  showToast(state.uiLang === 'en' ? 'Settings saved' : 'Настройки сохранены');

  try {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await loadSystemInfo();
  } catch (e) {
    console.error(e);
  }
}

function pullCustomOllamaModel() {
  const input = document.getElementById('custom-ollama-model-input');
  const val = input.value.trim();
  if (!val) return;
  input.value = '';
  pullOllamaModel(val);
}

function startOllamaPollingLoop(model) {
  clearInterval(window._ollamaPollTimer);
  window._ollamaPollTimer = setInterval(async () => {
    try {
      const res = await fetch('/api/ollama/status');
      const data = await res.json();
      updateOllamaUI(data);
      if (!data.pull_state || !data.pull_state.active) {
        clearInterval(window._ollamaPollTimer);
        window._ollamaPollTimer = null;
        await loadSystemInfo();
        if (data.pull_state && data.pull_state.progress === 100) {
          const pretty = formatModelName(model);
          showToast(state.uiLang === 'en' ? `Model ${pretty} ready!` : `Модель ${pretty} установлена и активна!`);
        }
      }
    } catch (e) {}
  }, 900);
}

async function pullOllamaModel(modelArg) {
  const model = (modelArg || document.getElementById('set-ollama-model').value || 'qwen2.5:7b').trim();
  let allowInstall = false;
  if (state.system && state.system.ollama && !state.system.ollama.installed) {
    allowInstall = confirm(state.uiLang === 'en'
      ? 'Ollama is not installed. Install it now via winget (package Ollama.Ollama, about 1 GB)?\n\nYou can also install it manually from https://ollama.com/download'
      : 'Ollama не установлена. Установить её сейчас через winget (пакет Ollama.Ollama, около 1 ГБ)?\n\nМожно установить и вручную: https://ollama.com/download');
    if (!allowInstall) return;
  }
  document.getElementById('set-ollama-model').value = model;
  const pretty = formatModelName(model);

  state.ollamaPullState = {
    active: true,
    model: model,
    progress: 1,
    status: state.uiLang === 'en' ? `Starting download of ${pretty}` : `Запуск установки ${pretty}`,
    logs: []
  };
  syncSidebarDownloadWidget();

  const dlPanel = document.getElementById('ollama-download-panel');
  if (dlPanel) dlPanel.classList.remove('hidden');
  document.getElementById('ollama-pull-status').textContent = state.ollamaPullState.status;
  document.getElementById('ollama-pull-pct').textContent = '1%';
  document.getElementById('ollama-pull-bar').style.width = '2%';

  await fetch('/api/ollama/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, allow_install: allowInstall })
  });

  startOllamaPollingLoop(model);
}

// --- Dictionary ---
async function loadDictionary() {
  const res = await fetch('/api/dictionary');
  state.dictionary = await res.json();
  document.getElementById('dict-count-badge').textContent = state.dictionary.filter(d => d.active).length;

  const box = document.getElementById('dictionary-list');
  if (state.dictionary.length === 0) {
    box.innerHTML = `<div class="p-6 text-center text-xs text-apple-tertiary">—</div>`;
    return;
  }

  box.innerHTML = state.dictionary.map(item => `
    <div class="flex items-center justify-between px-3.5 py-2 text-xs">
      <div class="flex items-center gap-2.5">
        <input type="checkbox" ${item.active ? 'checked' : ''}
          onchange="toggleDictItem(${item.id}, this.checked)"
          class="rounded bg-apple-bg border-white/20 text-apple-blue" />
        <span class="${item.active ? 'text-white' : 'text-apple-tertiary line-through'}">${escapeHtml(item.term)}</span>
        <span class="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">${escapeHtml(item.category)}</span>
      </div>
      <button onclick="deleteDictItem(${item.id})" title="${escapeHtml(t('tipDeleteDictTerm'))}" class="text-apple-tertiary hover:text-apple-red transition p-1">${SVG_ICONS.closeX}</button>
    </div>
  `).join('');
}

async function addDictionaryItems() {
  const input = document.getElementById('dict-term-input');
  const cat = document.getElementById('dict-category-input').value;
  if (!input.value.trim()) return;

  await fetch('/api/dictionary', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ term: input.value, category: cat })
  });
  input.value = '';
  await loadDictionary();
}

async function toggleDictItem(id, active) {
  await fetch(`/api/dictionary/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active })
  });
  await loadDictionary();
}

async function deleteDictItem(id) {
  await fetch(`/api/dictionary/${id}`, { method: 'DELETE' });
  await loadDictionary();
}

// --- Meetings List, Global Search & Sidebar ---
async function loadMeetingsList() {
  const res = await fetch('/api/meetings');
  state.meetings = await res.json();
  if (!state.globalSearchQuery) {
    renderMeetingsSidebar();
  }

  if (state.activeMeeting) {
    const updated = state.meetings.find(m => m.id === state.activeMeeting.id);
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
      <div onclick="selectMeeting(${q.id}, true)" title="${escapeHtml(detail)}"
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

// --- Questions about the whole library (answers cite transcript fragments) ---
const SVG_SPARK = '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>';
let _libraryAnswer = null;

function openLibraryAsk(question) {
  openModal('library');
  const input = document.getElementById('library-question');
  if (question) input.value = question;
  input.focus();
  if (!_libraryAnswer) document.getElementById('library-answer').innerHTML = '';
}

async function askLibrary() {
  const input = document.getElementById('library-question');
  const question = input.value.trim();
  if (!question) return;
  const box = document.getElementById('library-answer');
  const btn = document.getElementById('library-ask-btn');
  btn.disabled = true;
  box.innerHTML = `<div class="py-8 text-center text-xs text-apple-secondary animate-pulse">${escapeHtml(t('libraryAskWorking'))}</div>`;
  try {
    const res = await fetch('/api/library/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Error');
    _libraryAnswer = data;
    renderLibraryAnswer(data);
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-center text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderLibraryAnswer(data) {
  const box = document.getElementById('library-answer');
  if (!data.found) {
    box.innerHTML = `<div class="py-6 text-center text-xs text-apple-secondary">${escapeHtml(t('libraryAskNothing'))}</div>`;
    return;
  }
  const byN = Object.fromEntries((data.sources || []).map(s => [s.n, s]));
  // The answer comes from a model reading the recordings' speech: sanitized like any other LLM output
  let html = DOMPurify.sanitize(marked.parse(data.answer || ''), { FORBID_TAGS: ['style', 'form', 'input'], FORBID_ATTR: ['style'] });
  html = html.replace(/\[(\d{1,2})\]/g, (m, n) => byN[n]
    ? `<button class="lib-cite" onclick="openLibrarySource(${Number(n)})" title="${escapeHtml(byN[n].title)} · ${formatTime(byN[n].start)}">${n}</button>`
    : m);
  const sources = (data.sources || []).map(s => `
    <button onclick="openLibrarySource(${s.n})" class="w-full text-left px-2.5 py-1.5 rounded-md hover:bg-white/5 transition">
      <div class="flex items-center gap-2 text-[11px]">
        <span class="lib-cite">${s.n}</span>
        <span class="text-white truncate">${escapeHtml(s.title)}</span>
        <span class="text-apple-tertiary font-mono shrink-0">${escapeHtml(s.date)} · ${formatTime(s.start)}</span>
      </div>
      <div class="text-[11px] text-apple-secondary truncate mt-0.5 pl-7">${escapeHtml(s.speaker || '')}: ${escapeHtml(s.text)}</div>
    </button>`).join('');
  box.innerHTML = `
    <div class="markdown-body text-[13px]">${html}</div>
    <div class="mt-4 pt-3 border-t apple-border">
      <div class="text-[10.5px] uppercase tracking-wider text-apple-tertiary mb-1">${escapeHtml(t('libraryAskSources'))}</div>
      ${sources}
    </div>`;
}

function openLibrarySource(n) {
  const s = ((_libraryAnswer && _libraryAnswer.sources) || []).find(x => x.n === n);
  if (!s) return;
  closeModal('library');
  selectMeetingAndSeek(s.meeting_id, s.start);
}

async function selectMeetingAndSeek(meetingId, startSec) {
  await selectMeeting(meetingId, true);
  switchWorkspaceTab('transcript');
  setTimeout(() => seekPlayer(startSec), 220);
}

function renderMeetingsSidebar() {
  const listEl = document.getElementById('meetings-list');

  if (state.globalSearchQuery && state.globalSearchResults) {
    const askHtml = `
      <button onclick="openLibraryAsk(state.globalSearchQuery)" title="${escapeHtml(t('tipLibraryAsk'))}"
        class="w-full mb-1.5 px-2.5 py-2 rounded-lg border border-apple-blue/30 bg-apple-blue/10 hover:bg-apple-blue/20 text-left text-[11.5px] text-white transition flex items-center gap-2">
        ${SVG_SPARK}<span class="truncate">${escapeHtml(t('libraryAskFromSearch'))}</span>
      </button>`;
    if (state.globalSearchResults.length === 0) {
      listEl.innerHTML = askHtml + `<div class="px-3 py-8 text-center text-xs text-apple-tertiary">${escapeHtml(t('noMatches'))}</div>`;
      return;
    }
    listEl.innerHTML = askHtml + state.globalSearchResults.map(item => `
      <div class="rounded-lg bg-white/[0.03] border apple-border p-2.5 space-y-1.5 mb-1.5">
        <div onclick="selectMeeting(${item.meeting_id}, true)"
             title="${escapeHtml(item.title)}"
             class="text-xs font-semibold text-white hover:text-apple-blue cursor-pointer truncate">
          ${escapeHtml(item.title)}
        </div>
        ${(item.hits || []).map(h => `
          <div onclick="selectMeetingAndSeek(${item.meeting_id}, ${h.start})"
               title="${escapeHtml(t('tipSeekTimestamp'))}"
               class="px-2 py-1 rounded bg-black/30 hover:bg-apple-blue/20 cursor-pointer text-[11px] transition">
            <div class="flex items-center justify-between text-[10px] text-apple-blue font-mono">
              <span>${escapeHtml(h.speaker)}</span>
              <span>${formatTime(h.start)}</span>
            </div>
            <div class="text-apple-label/90 line-clamp-2 mt-0.5">${highlightSearch(escapeHtml(h.text), state.globalSearchQuery)}</div>
          </div>
        `).join('')}
      </div>
    `).join('');
    return;
  }

  if (state.meetings.length === 0) {
    listEl.innerHTML = `<div class="px-3 py-8 text-center text-xs text-apple-tertiary">${t('noRecordings')}</div>`;
    return;
  }

  listEl.innerHTML = state.meetings.map(m => {
    const isSelected = state.activeMeeting && state.activeMeeting.id === m.id;
    const isBusy = !DONE_STATUSES.includes(m.status);
    const spkCount = Object.keys(m.speakers || {}).length;

    return `
      <div onclick="selectMeeting(${m.id}, true)"
           oncontextmenu="openRecordingContextMenu(event, ${m.id})"
           class="group cursor-pointer px-3 py-2 rounded-lg transition relative ${
             isSelected ? 'bg-apple-blue text-white' : 'text-apple-label hover:bg-white/5'
           }">
        <div class="flex items-center justify-between gap-2">
          <span title="${escapeHtml(m.title)}" class="text-[12.5px] font-medium truncate">${escapeHtml(m.title)}</span>
          <button onclick="openRecordingContextMenu(event, ${m.id})"
            title="${escapeHtml(t('tipItemMenu'))}"
            class="opacity-0 group-hover:opacity-100 px-1 rounded hover:bg-black/20 text-xs shrink-0 transition">
            •••
          </button>
        </div>
        <div class="flex items-center justify-between text-[11px] mt-0.5 ${isSelected ? 'text-white/80' : 'text-apple-secondary'}">
          <span class="font-mono">${isBusy ? `${m.progress}%` : formatTime(m.duration)}</span>
          <span>${m.status === 'failed' ? (state.uiLang === 'en' ? 'Error' : 'Ошибка') : m.status === 'cancelled' ? t('statusCancelled') : `${spkCount || 1} ${t('spkShort')} · ${(m.language || 'ru').toUpperCase()}`}</span>
        </div>
        ${isBusy ? `
          <div class="w-full h-1 bg-black/25 rounded-full overflow-hidden mt-1.5">
            <div class="h-full bg-white transition-all duration-300" style="width: ${m.progress}%"></div>
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
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

async function handleFileSelect(fileList) {
  if (!fileList || fileList.length === 0) return;
  const statusEl = document.getElementById('upload-status-text');
  statusEl.classList.remove('hidden');
  statusEl.textContent = t('uploadingFiles').replace('{n}', fileList.length);

  const fd = new FormData();
  for (const f of fileList) fd.append('files', f);
  fd.append('model_name', document.getElementById('upload-model').value);
  fd.append('language', document.getElementById('upload-language').value);
  fd.append('num_speakers', document.getElementById('upload-speakers').value);

  try {
    const res = await fetch('/api/meetings/upload', { method: 'POST', body: fd });
    const data = await res.json();
    statusEl.classList.add('hidden');
    document.getElementById('file-input').value = '';
    closeModal('import');
    await loadMeetingsList();
    if (data.created && data.created.length > 0) {
      await selectMeeting(data.created[0].id, true);
      return data.created;
    }
  } catch (err) {
    statusEl.textContent = 'Error: ' + err;
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

// --- Active Meeting Workspace ---
async function selectMeeting(id, reloadPlayer = true) {
  const res = await fetch(`/api/meetings/${id}`);
  if (!res.ok) return;
  const m = await res.json();
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
}

function updateProcessingBanner(m) {
  const isBusy = !DONE_STATUSES.includes(m.status);
  const banner = document.getElementById('workspace-processing-banner');
  banner.classList.toggle('hidden', !isBusy && m.status !== 'failed' && m.status !== 'cancelled');
  document.getElementById('workspace-cancel-btn').classList.toggle('hidden', !isBusy);
  const bar = document.getElementById('workspace-progress-bar');
  const q = (state.queue || []).find(x => x.id === m.id);
  const running = isBusy && q && q.state === 'running';
  bar.classList.toggle('progress-live', !!running);
  document.getElementById('workspace-live-dot').classList.toggle('hidden', !running);
  document.getElementById('workspace-progress-time').textContent = running ? formatTiming(jobTiming(q, m.progress || 0)) : '';
  if (isBusy) {
    document.getElementById('workspace-stage-text').textContent = m.stage_text || 'Processing…';
    document.getElementById('workspace-progress-pct').textContent = `${m.progress}%`;
    bar.style.width = `${Math.max(running ? 3 : 0, m.progress || 0)}%`;
  } else {
    document.getElementById('workspace-stage-text').textContent =
      m.stage_text || (m.status === 'cancelled' ? t('statusCancelled') : 'Failed');
    document.getElementById('workspace-progress-pct').textContent = '';
    document.getElementById('workspace-progress-bar').style.width = '100%';
  }
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
  const box = document.getElementById('export-dub-section');
  if (!box) return;
  const dubs = availableDubs(state.activeMeeting);
  box.innerHTML = dubs.map(d => {
    const lang = escapeHtml(d.lang);
    const ext = String(d.path || '').split('.').pop().toUpperCase();
    return `
      <div class="px-2.5 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-apple-amber">${escapeHtml(t('exportDubHeader').replace('{lang}', d.lang.toUpperCase()))}</div>
      <button onclick="saveDubFile('${lang}', 'media')" title="${escapeHtml(t('tipExportDubMedia'))}" class="macos-menu-item"><span>${escapeHtml(t('exportDubMedia'))}</span><span class="text-[11px] opacity-60">${escapeHtml(ext)}</span></button>
      <button onclick="saveDubFile('${lang}', 'srt')" title="${escapeHtml(t('tipExportDubSrt'))}" class="macos-menu-item"><span>${escapeHtml(t('exportDubSrt'))}</span><span class="text-[11px] opacity-60">SRT</span></button>`;
  }).join('');
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

async function openDubModal() {
  const m = state.activeMeeting;
  if (!m) return;
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
    renderDubInfo(await res.json());
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
  showToast(t('dubQueued'));
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

function langName(code) {
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
  if (mode === 'burn' && !confirm(t('subsBurnConfirm'))) return;
  showToast(mode === 'soft' ? t('subsSoftWorking') : t('subsBurnWorking'));
  const res = await fetch(`/api/meetings/${m.id}/subtitles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lang: lang || null, variant, mode, ask_path: document.getElementById('subs-ask-path').checked }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  if (data.cancelled) return;
  if (data.queued) {
    closeModal('translate');
    showToast(t('subsBurnQueued').replace('{name}', data.filename));
    await loadMeetingsList();
    return;
  }
  showToast(`${t('exportSavedTo')} ${data.filename}`, { label: t('dubReveal'), onClick: () => revealPath(data.path) });
}

// Transcript view: original / translation / both
function renderTranslationControls() {
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
  const box = document.getElementById('export-menu-translated');
  if (!box) return;
  const langs = Object.keys(meetingTranslations(state.activeMeeting));
  box.innerHTML = langs.map(lang => {
    const L = escapeHtml(lang.toUpperCase());
    const item = (fmt, variant, label) =>
      `<button onclick="exportCurrentMeeting('${fmt}', {lang: '${lang}', variant: '${variant}'})" class="macos-menu-item"><span>${label}</span><span class="text-[11px] opacity-60">${fmt.toUpperCase()}</span></button>`;
    return `<div class="my-1 border-t border-white/10"></div>
      <div class="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-apple-secondary">${escapeHtml(t('exportTranslated').replace('{lang}', L))}</div>
      ${item('docx', 'translated', 'Word (.docx)')}${item('md', 'translated', 'Markdown (.md)')}${item('txt', 'translated', 'Text (.txt)')}${item('srt', 'translated', 'Subtitles (.srt)')}
      ${item('docx', 'bilingual', escapeHtml(t('exportBilingual')) + ' (.docx)')}${item('srt', 'bilingual', escapeHtml(t('exportBilingual')) + ' (.srt)')}`;
  }).join('');
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

  const allActive = !state.speakerFilter;
  let html = `
    <button onclick="filterBySpeaker(null)"
      title="${escapeHtml(t('tipFilterAllSpeakers'))}"
      class="ui-btn h-7 px-2.5 rounded-full text-[11px] ${
        allActive ? 'bg-white text-black' : 'bg-white/5 text-apple-secondary hover:text-white'
      }">
      ${t('allSpeakers')}
    </button>
  `;

  html += keys.map(spkId => {
    const s = speakers[spkId];
    const active = state.speakerFilter === spkId;
    return `
      <div class="inline-flex items-center h-7 rounded-full text-[11px] transition ${
        active ? 'bg-apple-blue text-white' : 'bg-white/5 text-apple-label hover:bg-white/10'
      }">
        <button onclick="filterBySpeaker('${spkId}')"
          title="${escapeHtml(t('tipFilterSpeaker'))}"
          class="pl-2.5 pr-1.5 h-full flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full" style="background:${s.color}"></span>
          <span class="font-medium">${escapeHtml(s.name)}</span>
        </button>
        <button onclick="previewSpeakerVoice(${s.sample_start || 0}, ${s.sample_end || 5})"
          title="${escapeHtml(t('tipPlayVoiceSample'))}"
          class="pr-2 pl-1 h-full flex items-center opacity-70 hover:opacity-100">
          ${SVG_ICONS.playMini}
        </button>
      </div>
    `;
  }).join('');

  container.innerHTML = html;
}

function renderSpeakersPanel() {
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
  state.activeMeeting = await res.json();
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

function translationHtml(seg, tr, query, secondary) {
  const staleBadge = tr.stale
    ? ` <span class="text-[10px] text-apple-amber align-middle" title="${escapeHtml(t('tipTranslationStale'))}">${escapeHtml(t('translationStale'))}</span>`
    : '';
  // Without a search the words are separate spans, so playback can follow them (see updatePlaybackHighlight)
  const body = !tr.text
    ? `<span class="italic text-apple-tertiary">${escapeHtml(t('translationMissing'))}</span>`
    : query
      ? highlightSearch(escapeHtml(tr.text), query)
      : tr.text.split(/(\s+)/).map(w => (w.trim() ? `<span class="tr-word">${escapeHtml(w)}</span>` : w)).join('');
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

  // A translation has no word timings: its words are followed in proportion to their length
  // across the time of the phrase, which is close enough to read along
  let trWordEl = null;
  const trWords = segEl ? segEl.querySelectorAll('.tr-word') : [];
  if (trWords.length) {
    const seg = tx.items[segIdx];
    const span = Math.max(0.001, Number(seg.end) - Number(seg.start));
    const target = Math.min(1, Math.max(0, (cur - Number(seg.start)) / span));
    const lens = Array.from(trWords, w => w.textContent.length + 1);
    const total = lens.reduce((a, b) => a + b, 0);
    let acc = 0;
    for (let i = 0; i < lens.length; i++) {
      acc += lens[i];
      if (acc / total >= target) { trWordEl = trWords[i]; break; }
    }
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
    state.activeMeeting = await res.json();
    renderSpeakerPillsBar();
    renderSpeakersPanel();
    renderTranscript();
    showToast(state.uiLang === 'en' ? 'Segments merged' : 'Реплики объединены');
  }
}

async function cleanActiveTranscriptFillers() {
  if (!state.activeMeeting) return;
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/clean-fillers`, {
    method: 'POST'
  });
  if (!res.ok) return;
  const data = await res.json();
  state.activeMeeting = data.meeting;
  renderTranscript();
  const cnt = data.removed_count || 0;
  const meetingId = state.activeMeeting.id;
  showToast(
    cnt > 0
      ? (state.uiLang === 'en' ? `Cleaned ${cnt} filler words` : `Удалено слов-паразитов и повторов: ${cnt}`)
      : (state.uiLang === 'en' ? 'Transcript is already clean' : 'Текст уже чистый'),
    cnt > 0 ? { label: t('undo'), onClick: () => restoreSegments(meetingId) } : undefined
  );
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
  state.activeMeeting = await res.json();
  renderTranscript();
}

async function changeSegmentSpeaker(segId, newSpeaker) {
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/segments/${segId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speaker: newSpeaker })
  });
  state.activeMeeting = await res.json();
  renderSpeakerPillsBar();
  renderSpeakersPanel();
  renderTranscript();
}

// =========================================================================
// SOUNDCLOUD-STYLE WAVEFORM VISUALIZER FOR BOTTOM PLAYER BAR
// =========================================================================
async function buildPlayerWaveformEnvelope(meeting) {
  // Flat placeholder until the real loudness envelope of the recording is loaded
  state.playerPeaks = new Array(120).fill(0.14);
  renderPlayerWaveform(0);
  if (!meeting || !meeting.audio_path) return;
  try {
    const res = await fetch(`/api/meetings/${meeting.id}/peaks?bars=120`);
    if (!res.ok) return;
    const data = await res.json();
    if (!state.activeMeeting || state.activeMeeting.id !== meeting.id) return;
    if (Array.isArray(data.peaks) && data.peaks.length > 0) {
      state.playerPeaks = data.peaks.map(v => Math.max(0.08, Number(v) || 0));
      const player = document.getElementById('media-player');
      const dur = (player && player.duration) || meeting.duration || 1;
      renderPlayerWaveform(((player && player.currentTime) || 0) / dur);
    }
  } catch (e) {}
}

function renderPlayerWaveform(progressRatio = 0) {
  const canvas = document.getElementById('player-waveform-canvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(200, rect.width || 540);
  const h = Math.max(24, rect.height || 32);

  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  const ctx = canvas.getContext('2d');
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const peaks = state.playerPeaks.length ? state.playerPeaks : new Array(110).fill(0.25);
  const n = peaks.length;
  const gap = 2;
  const barW = Math.max(2, (w - gap * (n - 1)) / n);
  const centerY = h * 0.58;
  const maxTop = h * 0.50;
  const maxBot = h * 0.28;

  for (let i = 0; i < n; i++) {
    const x = i * (barW + gap);
    const barRatio = i / n;
    const amp = Math.max(0.10, Math.min(1.0, peaks[i]));
    const topH = Math.max(2, amp * maxTop);
    const botH = Math.max(1.5, amp * maxBot * 0.65);

    let topColor = 'rgba(255, 255, 255, 0.24)';
    let botColor = 'rgba(255, 255, 255, 0.11)';

    if (barRatio <= progressRatio) {
      topColor = '#0A84FF';
      botColor = 'rgba(10, 132, 255, 0.42)';
    } else if (state.playerHoverRatio >= 0 && barRatio <= state.playerHoverRatio) {
      topColor = 'rgba(10, 132, 255, 0.65)';
      botColor = 'rgba(10, 132, 255, 0.25)';
    }

    ctx.fillStyle = topColor;
    ctx.beginPath();
    ctx.roundRect(x, centerY - topH, barW, topH, 1.5);
    ctx.fill();

    ctx.fillStyle = botColor;
    ctx.beginPath();
    ctx.roundRect(x, centerY + 1.5, barW, botH, 1);
    ctx.fill();
  }

  ctx.restore();
}

// --- Player Transport, Volume Control & Studio Voice Enhancer ---
function togglePlayPause() {
  const player = document.getElementById('media-player');
  if (!player) return;
  if (player.paused) player.play();
  else player.pause();
}

function skipPlayer(deltaSec) {
  const player = document.getElementById('media-player');
  if (!player) return;
  player.currentTime = Math.max(0, player.currentTime + deltaSec);
}

function seekPlayer(seconds) {
  const player = document.getElementById('media-player');
  if (!player) return;
  clearTimeout(state.voicePreviewTimer);
  const target = Math.max(0, Number(seconds) || 0);
  // Until the file's metadata is loaded (just opened, or the audio track was switched) a seek
  // can be dropped and playback starts from 0: apply it once the player knows the duration
  if (player.readyState < 1) {
    player.addEventListener('loadedmetadata', () => { player.currentTime = target; player.play(); }, { once: true });
    return;
  }
  player.currentTime = target;
  player.play();
}

function changePlayerVolume(val) {
  const v = Math.max(0, Math.min(100, Number(val)));
  state.volume = v;
  state.muted = (v === 0);
  localStorage.setItem('verbascribe_volume', String(v));

  const player = document.getElementById('media-player');
  if (player) {
    player.muted = state.muted;
    player.volume = v / 100;
  }
  updateVolumeUI();
}

function toggleMutePlayer() {
  state.muted = !state.muted;
  const player = document.getElementById('media-player');
  if (player) {
    player.muted = state.muted;
    if (!state.muted && state.volume === 0) {
      state.volume = 80;
      player.volume = 0.8;
    }
  }
  updateVolumeUI();
}

function updateVolumeUI() {
  const slider = document.getElementById('player-volume-slider');
  const label = document.getElementById('player-volume-label');
  const iconOn = document.getElementById('icon-vol-on');
  const iconMuted = document.getElementById('icon-vol-muted');

  const effectiveVol = state.muted ? 0 : state.volume;
  if (slider) slider.value = effectiveVol;
  if (label) label.textContent = `${effectiveVol}%`;
  if (iconOn && iconMuted) {
    iconOn.classList.toggle('hidden', effectiveVol === 0);
    iconMuted.classList.toggle('hidden', effectiveVol > 0);
  }
}

function cyclePlaybackSpeed() {
  state.speedIndex = (state.speedIndex + 1) % state.playbackSpeeds.length;
  const rate = state.playbackSpeeds[state.speedIndex];
  const player = document.getElementById('media-player');
  if (player) player.playbackRate = rate;
  document.getElementById('speed-cycle-btn').textContent = `${rate.toFixed(rate % 1 === 0 ? 1 : 2)}x`;
}

function toggleStudioVoiceEnhancer() {
  const player = document.getElementById('media-player');
  if (!player) return;

  if (!window._audioCtx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    const source = ctx.createMediaElementSource(player);

    // 1. High-pass filter at 85 Hz (removes low rumble & mic hum)
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 85;

    // 2. Presence boost around 3.1 kHz (speech clarity & intelligibility)
    const presence = ctx.createBiquadFilter();
    presence.type = 'peaking';
    presence.frequency.value = 3100;
    presence.Q.value = 1.0;
    presence.gain.value = 4.5;

    // 3. Low-pass filter at 7.8 kHz (removes high hiss)
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 7800;

    // 4. Broadcast compressor (levels quiet and loud voices)
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -26;
    comp.knee.value = 18;
    comp.ratio.value = 4.5;
    comp.attack.value = 0.005;
    comp.release.value = 0.18;

    const makeupGain = ctx.createGain();
    makeupGain.gain.value = 1.45;

    window._audioCtx = ctx;
    window._audioNodes = { source, hp, presence, lp, comp, makeupGain };
  }

  state.studioVoiceActive = !state.studioVoiceActive;
  const { source, hp, presence, lp, comp, makeupGain } = window._audioNodes;
  const ctx = window._audioCtx;
  if (ctx.state === 'suspended') ctx.resume();

  source.disconnect();
  hp.disconnect();
  presence.disconnect();
  lp.disconnect();
  comp.disconnect();
  makeupGain.disconnect();

  if (state.studioVoiceActive) {
    source.connect(hp);
    hp.connect(presence);
    presence.connect(lp);
    lp.connect(comp);
    comp.connect(makeupGain);
    makeupGain.connect(ctx.destination);
  } else {
    source.connect(ctx.destination);
  }

  const btn = document.getElementById('studio-voice-btn');
  if (btn) {
    btn.className = state.studioVoiceActive
      ? 'ui-btn ui-btn-active'
      : 'ui-btn ui-btn-subtle';
  }
  showToast(
    state.studioVoiceActive
      ? (state.uiLang === 'en' ? 'Studio Voice Enabled' : 'Студийный голос включён (шумодав + компрессор)')
      : (state.uiLang === 'en' ? 'Original Audio' : 'Исходный звук')
  );
}

// =========================================================================
// AUDIO SOURCE SELECTION, SETTINGS SIGNAL TESTER & SOUNDCLOUD LIVE RECORDER
// =========================================================================
let _mediaRecorder = null;
let _recChunks = [];
let _recStreams = [];
let _recAudioCtx = null;
let _recStartTime = 0;
let _recTimerInterval = null;
let _recAnimFrame = null;
let _recWaveHistory = [];
// System audio recorded by the app itself (no screen-share prompt); see app/system_audio.py
let _recCaptureId = null;
let _recSysLevel = 0;
let _recSysTimer = null;

let _settingsTestStreams = [];
let _settingsTestCtx = null;
let _settingsTestAnim = null;
let _settingsWaveHistory = [];

async function populateAudioInputDevices() {
  const settingsSelect = document.getElementById('set-record-mic');
  const modalSelect = document.getElementById('record-mic-select');
  const savedMic = (state.system && state.system.settings && state.system.settings.record_mic_device) || '';

  try {
    const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const devices = await navigator.mediaDevices.enumerateDevices();
    tempStream.getTracks().forEach(tr => tr.stop());

    const mics = devices.filter(d => d.kind === 'audioinput');
    const optionsHtml = mics.map((m, i) => {
      const label = m.label || (state.uiLang === 'en' ? `Microphone ${i + 1}` : `Микрофон ${i + 1}`);
      const sel = m.deviceId === savedMic ? 'selected' : '';
      return `<option value="${escapeHtml(m.deviceId)}" ${sel}>${escapeHtml(label)}</option>`;
    }).join('');

    if (settingsSelect) settingsSelect.innerHTML = optionsHtml || `<option value="">Default Microphone</option>`;
    if (modalSelect) modalSelect.innerHTML = optionsHtml || `<option value="">Default Microphone</option>`;
  } catch (e) {
    const fallback = `<option value="">${state.uiLang === 'en' ? 'Default System Microphone' : 'Системный микрофон по умолчанию'}</option>`;
    if (settingsSelect) settingsSelect.innerHTML = fallback;
    if (modalSelect) modalSelect.innerHTML = fallback;
  }
}

function onSettingsRecordSourceChange() {
  const val = document.getElementById('set-record-source').value;
  const recSel = document.getElementById('record-source-select');
  if (recSel) recSel.value = val;
  if (_settingsTestCtx) {
    stopSettingsAudioTest();
    toggleSettingsAudioTest();
  }
}

function onSettingsRecordMicChange() {
  const val = document.getElementById('set-record-mic').value;
  const recMic = document.getElementById('record-mic-select');
  if (recMic) recMic.value = val;
  if (_settingsTestCtx) {
    stopSettingsAudioTest();
    toggleSettingsAudioTest();
  }
}

function syncRecordModalToSettings() {
  const src = document.getElementById('record-source-select').value;
  const mic = document.getElementById('record-mic-select').value;
  if (document.getElementById('set-record-source')) {
    document.getElementById('set-record-source').value = src;
  }
  if (document.getElementById('set-record-mic')) {
    document.getElementById('set-record-mic').value = mic;
  }
  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      settings: {
        record_source_mode: src,
        record_mic_device: mic
      }
    })
  });
}

async function buildCaptureStream(sourceMode, micDeviceId, systemByApp = false) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  const dest = ctx.createMediaStreamDestination();
  const acquiredStreams = [];

  const needMic = sourceMode === 'mic' || sourceMode === 'both';
  // When the app records system audio itself, the page does not ask for a screen share
  const needSystem = !systemByApp && (sourceMode === 'system' || sourceMode === 'both');

  if (needMic) {
    const micConstraints = {
      audio: micDeviceId
        ? { deviceId: { exact: micDeviceId }, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        : { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    };
    const micStream = await navigator.mediaDevices.getUserMedia(micConstraints);
    acquiredStreams.push(micStream);
    const micNode = ctx.createMediaStreamSource(micStream);
    micNode.connect(dest);
  }

  if (needSystem) {
    const sysStream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false
      }
    });
    acquiredStreams.push(sysStream);
    sysStream.getVideoTracks().forEach(vt => vt.stop());
    if (sysStream.getAudioTracks().length === 0) {
      throw new Error(
        state.uiLang === 'en'
          ? 'Please enable "Share system audio" in the screen share prompt.'
          : 'При выборе окна/экрана обязательно включите галочку «Поделиться системным звуком» (Share audio).'
      );
    }
    const sysNode = ctx.createMediaStreamSource(new MediaStream(sysStream.getAudioTracks()));
    sysNode.connect(dest);
  }

  return { ctx, mixedStream: dest.stream, acquiredStreams };
}

function drawIdleSoundCloudBars(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(220, rect.width || 400);
  const h = Math.max(40, rect.height || 64);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);

  const ctx = canvas.getContext('2d');
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const numBars = 68;
  const gap = 2.5;
  const barW = Math.max(2, (w - gap * (numBars - 1)) / numBars);
  const midY = h / 2;

  ctx.fillStyle = 'rgba(255, 255, 255, 0.14)';
  for (let i = 0; i < numBars; i++) {
    const x = i * (barW + gap);
    ctx.beginPath();
    ctx.roundRect(x, midY - 2, barW, 4, 1);
    ctx.fill();
  }
  ctx.restore();
}

function drawLiveSoundCloudBars(canvasId, historyArr, activeColor = '#FF453A') {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(220, rect.width || 400);
  const h = Math.max(40, rect.height || 64);

  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }

  const ctx = canvas.getContext('2d');
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const numBars = 68;
  const gap = 2.5;
  const barW = Math.max(2, (w - gap * (numBars - 1)) / numBars);
  const midY = h * 0.56;
  const maxTop = h * 0.48;
  const maxBot = h * 0.30;

  for (let i = 0; i < numBars; i++) {
    const val = historyArr[i] || 0.04;
    const isSignal = val > 0.09;
    const topH = Math.max(2, val * maxTop);
    const botH = Math.max(1.5, val * maxBot * 0.65);
    const x = i * (barW + gap);

    ctx.fillStyle = isSignal ? activeColor : 'rgba(255, 255, 255, 0.22)';
    ctx.beginPath();
    ctx.roundRect(x, midY - topH, barW, topH, 1.5);
    ctx.fill();

    ctx.fillStyle = isSignal ? 'rgba(255, 69, 58, 0.38)' : 'rgba(255, 255, 255, 0.10)';
    if (activeColor === '#0A84FF' && isSignal) {
      ctx.fillStyle = 'rgba(10, 132, 255, 0.38)';
    }
    ctx.beginPath();
    ctx.roundRect(x, midY + 1.5, barW, botH, 1);
    ctx.fill();
  }

  ctx.restore();
}

function computeRmsAndDb(analyser, timeBuf) {
  analyser.getByteTimeDomainData(timeBuf);
  let sumSq = 0;
  for (let i = 0; i < timeBuf.length; i++) {
    const norm = (timeBuf[i] - 128) / 128.0;
    sumSq += norm * norm;
  }
  const rms = Math.sqrt(sumSq / timeBuf.length);
  const db = rms > 0.0001 ? Math.max(-60, Math.round(20 * Math.log10(rms))) : -60;
  const visualAmp = Math.min(1.0, Math.max(0.04, Math.pow(rms * 4.2, 0.65)));
  return { rms, db, visualAmp };
}

async function toggleSettingsAudioTest() {
  if (_settingsTestCtx) {
    stopSettingsAudioTest();
    return;
  }
  try {
    const sourceMode = document.getElementById('set-record-source').value || 'mic';
    const micId = document.getElementById('set-record-mic').value || '';
    const { ctx, mixedStream, acquiredStreams } = await buildCaptureStream(sourceMode, micId);
    _settingsTestCtx = ctx;
    _settingsTestStreams = acquiredStreams;

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    const srcNode = ctx.createMediaStreamSource(mixedStream);
    srcNode.connect(analyser);
    const timeBuf = new Uint8Array(analyser.fftSize);
    _settingsWaveHistory = new Array(68).fill(0.04);

    document.getElementById('btn-test-settings-audio-label').textContent = t('stopTestAudioBtn');
    document.getElementById('btn-test-settings-audio').className = 'ui-btn ui-btn-active h-6 px-2.5 text-[11px]';

    function step() {
      if (!_settingsTestCtx) return;
      const { db, visualAmp } = computeRmsAndDb(analyser, timeBuf);
      _settingsWaveHistory.push(visualAmp);
      if (_settingsWaveHistory.length > 68) _settingsWaveHistory.shift();

      drawLiveSoundCloudBars('settings-waveform-canvas', _settingsWaveHistory, '#0A84FF');
      document.getElementById('settings-db-readout').textContent = `${db} dB`;

      const hasSound = db > -44;
      document.getElementById('settings-audio-dot').className = hasSound
        ? 'w-2 h-2 rounded-full bg-apple-green animate-pulse'
        : 'w-2 h-2 rounded-full bg-apple-amber';
      document.getElementById('settings-audio-status').textContent = hasSound
        ? (state.uiLang === 'en' ? 'Signal detected — audio is active' : 'Звук есть — сигнал поступает')
        : (state.uiLang === 'en' ? 'Listening — speak to test level' : 'Ожидание сигнала — скажите что-нибудь');

      _settingsTestAnim = requestAnimationFrame(step);
    }
    step();
  } catch (err) {
    stopSettingsAudioTest();
    alert(err.message || err);
  }
}

function stopSettingsAudioTest() {
  if (_settingsTestAnim) cancelAnimationFrame(_settingsTestAnim);
  _settingsTestAnim = null;
  if (_settingsTestStreams) {
    _settingsTestStreams.forEach(s => s.getTracks().forEach(tr => tr.stop()));
    _settingsTestStreams = [];
  }
  if (_settingsTestCtx) {
    try { _settingsTestCtx.close(); } catch (e) {}
    _settingsTestCtx = null;
  }
  const btnLabel = document.getElementById('btn-test-settings-audio-label');
  if (btnLabel) btnLabel.textContent = t('testAudioBtn');
  const btn = document.getElementById('btn-test-settings-audio');
  if (btn) btn.className = 'ui-btn ui-btn-subtle h-6 px-2.5 text-[11px]';
  const dot = document.getElementById('settings-audio-dot');
  if (dot) dot.className = 'w-2 h-2 rounded-full bg-apple-tertiary';
  const st = document.getElementById('settings-audio-status');
  if (st) st.textContent = t('audioMeterIdle');
  const dbEl = document.getElementById('settings-db-readout');
  if (dbEl) dbEl.textContent = '-60 dB';
}

async function openRecordModal() {
  openModal('record');
  initLiveCaptionControls();
  await populateAudioInputDevices();

  const savedSource = (state.system && state.system.settings && state.system.settings.record_source_mode) || 'both';
  document.getElementById('record-source-select').value = savedSource;
  const dataDir = state.system && state.system.paths && state.system.paths.data;
  document.getElementById('record-save-hint').textContent = dataDir ? t('recordSaveHint').replace('{dir}', `${dataDir}\\uploads`) : '';

  const nowStr = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }).replace(':', '-');
  document.getElementById('record-title-input').value = `Запись ${nowStr}`;
  document.getElementById('record-timer-display').textContent = '00:00';
  document.getElementById('record-level-bar').style.width = '2%';
  document.getElementById('record-db-display').textContent = '-60 dB';
  document.getElementById('record-signal-badge').textContent = state.uiLang === 'en' ? 'Ready' : 'Готов к записи';
  document.getElementById('record-signal-badge').className = 'px-2 py-0.5 rounded-full text-[11px] font-medium bg-white/5 text-apple-secondary';

  setTimeout(() => drawIdleSoundCloudBars('record-waveform-canvas'), 40);
}

// --- Live captions while recording ---
// The same mixed stream that is recorded is tapped by an AudioWorklet, resampled to 16 kHz mono
// 16-bit PCM and posted about once a second; each answer carries new final lines, their
// translations and the current draft line.
const liveCap = { session: null, node: null, pcm: [], pcmLen: 0, inFlight: false, timer: null, lines: new Map(), partial: '', lastId: 0 };

function liveCaptionPrefs() {
  try { return JSON.parse(localStorage.getItem('verbascribe_live_captions') || '{}'); } catch (e) { return {}; }
}

function saveLiveCaptionPrefs() {
  const prefs = {
    on: document.getElementById('live-captions-toggle').checked,
    translate: document.getElementById('live-translate-select').value,
  };
  try { localStorage.setItem('verbascribe_live_captions', JSON.stringify(prefs)); } catch (e) {}
}

function initLiveCaptionControls() {
  const prefs = liveCaptionPrefs();
  const names = t('dubLangNames') || {};
  const sel = document.getElementById('live-translate-select');
  sel.innerHTML = `<option value="">${escapeHtml(t('liveNoTranslation'))}</option>`
    + Object.keys(names).sort((a, b) => names[a].localeCompare(names[b]))
      .map(c => `<option value="${c}">${escapeHtml(t('liveTranslateTo').replace('{lang}', names[c]))}</option>`).join('');
  sel.value = prefs.translate || '';
  document.getElementById('live-captions-toggle').checked = !!prefs.on;
  const box = document.getElementById('live-captions');
  box.classList.add('hidden');
  box.innerHTML = '';
}

function downsampleTo16k(input, rate) {
  if (rate === 16000) return input;
  const ratio = rate / 16000;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio), b = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += input[j];
    out[i] = sum / Math.max(1, b - a);
  }
  return out;
}

async function startLiveCaptions(ctx, stream) {
  liveCap.lines = new Map();
  liveCap.partial = '';
  liveCap.lastId = 0;
  liveCap.pcm = [];
  liveCap.pcmLen = 0;
  const box = document.getElementById('live-captions');
  box.classList.remove('hidden');
  box.innerHTML = `<div class="text-[11.5px] text-apple-tertiary">${escapeHtml(t('liveStarting'))}</div>`;
  const res = await fetch('/api/live/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      lang: (state.system && state.system.settings && state.system.settings.language) || 'auto',
      translate_to: document.getElementById('live-translate-select').value || null,
      capture_id: _recCaptureId,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'Error');
  liveCap.session = data.session;
  await ctx.audioWorklet.addModule('/static/live-capture-worklet.js');
  liveCap.node = new AudioWorkletNode(ctx, 'pcm-capture');
  ctx.createMediaStreamSource(stream).connect(liveCap.node);
  // The worklet must be pulled by the graph; a silent gain keeps it out of the speakers
  const mute = ctx.createGain();
  mute.gain.value = 0;
  liveCap.node.connect(mute).connect(ctx.destination);
  liveCap.node.port.onmessage = (e) => {
    const pcm = downsampleTo16k(e.data, ctx.sampleRate);
    liveCap.pcm.push(pcm);
    liveCap.pcmLen += pcm.length;
  };
  liveCap.timer = setInterval(sendLiveAudio, 1000);
}

async function sendLiveAudio() {
  if (!liveCap.session || liveCap.inFlight || !liveCap.pcmLen) return;
  const buf = new Int16Array(liveCap.pcmLen);
  let pos = 0;
  for (const chunk of liveCap.pcm) {
    for (let i = 0; i < chunk.length; i++) buf[pos++] = Math.max(-32768, Math.min(32767, Math.round(chunk[i] * 32767)));
  }
  liveCap.pcm = [];
  liveCap.pcmLen = 0;
  liveCap.inFlight = true;
  const session = liveCap.session;
  try {
    const res = await fetch(`/api/live/${session}/audio?since=${liveCap.lastId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: buf.buffer,
    });
    const data = await res.json().catch(() => ({}));
    if (session !== liveCap.session) return;
    if (!res.ok) throw new Error(data.detail || 'Error');
    for (const ln of data.lines || []) {
      liveCap.lines.set(ln.id, { ...(liveCap.lines.get(ln.id) || {}), ...ln });
      liveCap.lastId = Math.max(liveCap.lastId, ln.id);
    }
    for (const [id, tr] of Object.entries(data.translations || {})) {
      const ln = liveCap.lines.get(Number(id));
      if (ln) ln.translation = tr;
    }
    liveCap.partial = data.partial || '';
    renderLiveCaptions(data.error);
  } catch (e) {
    renderLiveCaptionsError(e.message);
  } finally {
    liveCap.inFlight = false;
  }
}

function renderLiveCaptions(error) {
  const box = document.getElementById('live-captions');
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const translating = !!document.getElementById('live-translate-select').value;
  const lines = [...liveCap.lines.values()].sort((a, b) => a.id - b.id).map(ln => `
    <div>
      <div class="text-white">${escapeHtml(ln.text)}</div>
      ${translating ? `<div class="text-apple-blue/90 text-[12.5px]">${ln.translation ? escapeHtml(ln.translation) : '<span class="text-apple-tertiary">…</span>'}</div>` : ''}
    </div>`).join('');
  box.innerHTML = (lines || '')
    + (liveCap.partial ? `<div class="text-apple-secondary italic">${escapeHtml(liveCap.partial)}</div>` : '')
    + (!lines && !liveCap.partial ? `<div class="text-[11.5px] text-apple-tertiary">${escapeHtml(t('liveListening'))}</div>` : '')
    + (error ? `<div class="text-[11px] text-apple-amber">${escapeHtml(error)}</div>` : '');
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function renderLiveCaptionsError(msg) {
  const box = document.getElementById('live-captions');
  box.classList.remove('hidden');
  box.insertAdjacentHTML('beforeend', `<div class="text-[11px] text-apple-red">${escapeHtml(t('liveFailed'))}: ${escapeHtml(msg || '')}</div>`);
}

function stopLiveCaptions() {
  clearInterval(liveCap.timer);
  liveCap.timer = null;
  try { liveCap.node && liveCap.node.disconnect(); } catch (e) {}
  liveCap.node = null;
  if (liveCap.session) {
    fetch(`/api/live/${liveCap.session}/stop`, { method: 'POST' }).catch(() => {});
    liveCap.session = null;
  }
}

// The tray icon shows "recording" while the page records (app/app_status.py)
function reportRecordingState(active) {
  fetch('/api/ui/state', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ recording: !!active }),
  }).catch(() => {});
}

// Commands from the tray menu (run.py TrayController); the window is already shown
window.trayCommand = function (cmd) {
  if (cmd === 'record') {
    if (_mediaRecorder && _mediaRecorder.state === 'recording') {
      stopAndTranscribeRecording();
      return;
    }
    // System audio is recorded by the app, so recording can start without a click in the window
    openRecordModal().then(() => startLiveRecording());
  } else if (cmd === 'import') {
    openModal('import');
  } else if (cmd === 'ask') {
    openLibraryAsk('');
  } else if (cmd === 'settings') {
    openModal('settings');
  }
};

async function startLiveRecording() {
  try {
    const sourceMode = document.getElementById('record-source-select').value || 'mic';
    const micId = document.getElementById('record-mic-select').value || '';
    syncRecordModalToSettings();

    // System audio: recorded by the app (no prompt); the browser's screen share is only a fallback
    _recCaptureId = null;
    _recSysLevel = 0;
    if (sourceMode === 'system' || sourceMode === 'both') {
      try {
        const res = await fetch('/api/record/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ system: true }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.capture_id) _recCaptureId = data.capture_id;
        else console.warn('System audio by the app is unavailable:', data.detail);
      } catch (e) {
        console.warn(e);
      }
    }
    const { ctx, mixedStream, acquiredStreams } = await buildCaptureStream(sourceMode, micId, !!_recCaptureId);
    _recAudioCtx = ctx;
    _recStreams = acquiredStreams;
    _recChunks = [];

    // With system audio from the app and no microphone there is nothing for the page to record
    _mediaRecorder = null;
    if (!(_recCaptureId && sourceMode === 'system')) {
      _mediaRecorder = new MediaRecorder(mixedStream);
      _mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) _recChunks.push(e.data);
      };
      _mediaRecorder.start(250);
    }
    if (_recCaptureId) {
      clearInterval(_recSysTimer);
      _recSysTimer = setInterval(async () => {
        try {
          const r = await fetch(`/api/record/${_recCaptureId}/level`);
          if (r.ok) _recSysLevel = (await r.json()).level || 0;
        } catch (e) {}
      }, 200);
    }
    if (document.getElementById('live-captions-toggle').checked) {
      startLiveCaptions(ctx, mixedStream).catch(err => {
        console.error(err);
        renderLiveCaptionsError(err.message);
      });
    }

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    const srcNode = ctx.createMediaStreamSource(mixedStream);
    srcNode.connect(analyser);
    const timeBuf = new Uint8Array(analyser.fftSize);
    _recWaveHistory = new Array(68).fill(0.04);

    function updateMeter() {
      if (!_recAudioCtx) return;
      let { db, visualAmp } = computeRmsAndDb(analyser, timeBuf);
      if (_recCaptureId && _recSysLevel > 0.0001) {
        // The louder of the microphone (page) and the system audio (app)
        const sysDb = Math.max(-60, Math.round(20 * Math.log10(_recSysLevel)));
        if (sysDb > db) {
          db = sysDb;
          visualAmp = Math.min(1.0, Math.max(0.04, Math.pow(_recSysLevel * 4.2, 0.65)));
        }
      }
      _recWaveHistory.push(visualAmp);
      if (_recWaveHistory.length > 68) _recWaveHistory.shift();

      drawLiveSoundCloudBars('record-waveform-canvas', _recWaveHistory, '#FF453A');

      const pct = Math.min(100, Math.max(2, Math.round(((db + 60) / 60) * 100)));
      const levelBar = document.getElementById('record-level-bar');
      levelBar.style.width = `${pct}%`;
      levelBar.className = db > -6
        ? 'h-full bg-apple-red transition-all duration-75'
        : (db > -42 ? 'h-full bg-apple-green transition-all duration-75' : 'h-full bg-apple-secondary transition-all duration-75');

      document.getElementById('record-db-display').textContent = `${db} dB`;

      const badge = document.getElementById('record-signal-badge');
      if (db > -44) {
        badge.textContent = state.uiLang === 'en' ? 'Audio Active · Recording' : 'Звук есть · Идёт запись';
        badge.className = 'px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-apple-green/20 text-apple-green';
      } else {
        badge.textContent = state.uiLang === 'en' ? 'Silence' : 'Тишина на входе';
        badge.className = 'px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-white/5 text-apple-secondary';
      }

      _recAnimFrame = requestAnimationFrame(updateMeter);
    }
    updateMeter();

    _recStartTime = Date.now();
    clearInterval(_recTimerInterval);
    _recTimerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - _recStartTime) / 1000);
      document.getElementById('record-timer-display').textContent = formatTime(elapsed);
    }, 400);

    reportRecordingState(true);
    document.getElementById('btn-start-rec').classList.add('hidden');
    document.getElementById('btn-stop-rec').classList.remove('hidden');
    document.getElementById('rec-dot-indicator').classList.add('animate-ping');
    document.getElementById('record-status-hint').textContent =
      state.uiLang === 'en'
        ? 'Recording live audio waveform…'
        : 'Звуковая дорожка записывается в реальном времени…';
  } catch (err) {
    _cleanupRecordingResources();
    alert((state.uiLang === 'en' ? 'Audio capture error: ' : 'Не удалось начать запись звука: ') + err.message);
  }
}

function _cleanupRecordingResources() {
  reportRecordingState(false);
  clearInterval(_recSysTimer);
  _recSysTimer = null;
  stopLiveCaptions();
  clearInterval(_recTimerInterval);
  if (_recAnimFrame) cancelAnimationFrame(_recAnimFrame);
  _recAnimFrame = null;
  if (_recStreams) {
    _recStreams.forEach(s => s.getTracks().forEach(tr => tr.stop()));
    _recStreams = [];
  }
  if (_recAudioCtx) {
    try { _recAudioCtx.close(); } catch (e) {}
    _recAudioCtx = null;
  }
  document.getElementById('btn-start-rec').classList.remove('hidden');
  document.getElementById('btn-stop-rec').classList.add('hidden');
  document.getElementById('rec-dot-indicator').classList.remove('animate-ping');
}

function cancelLiveRecording() {
  if (_mediaRecorder && _mediaRecorder.state !== 'inactive') {
    _mediaRecorder.stop();
  }
  if (_recCaptureId) {
    fetch(`/api/record/${_recCaptureId}/cancel`, { method: 'POST' }).catch(() => {});
    _recCaptureId = null;
  }
  _cleanupRecordingResources();
  closeModal('record');
}

async function stopAndTranscribeRecording() {
  if (!_mediaRecorder && !_recCaptureId) return;
  document.getElementById('record-status-hint').textContent =
    state.uiLang === 'en' ? 'Saving and queuing transcription…' : 'Сохранение и отправка на расшифровку…';

  if (_mediaRecorder) {
    await new Promise(resolve => {
      _mediaRecorder.onstop = resolve;
      _mediaRecorder.stop();
    });
  }

  const blob = _mediaRecorder ? new Blob(_recChunks, { type: 'audio/webm' }) : null;
  const captureId = _recCaptureId;
  _recCaptureId = null;
  _cleanupRecordingResources();
  closeModal('record');

  const rawTitle = (document.getElementById('record-title-input').value || 'Запись').trim();
  const safeName = rawTitle.replace(/[\\/:*?"<>|]/g, '_') + '.webm';

  let created;
  if (captureId) {
    // The app mixes its system-audio recording with the microphone and queues the result
    const fd = new FormData();
    fd.append('title', rawTitle);
    if (blob) fd.append('file', new File([blob], safeName, { type: 'audio/webm' }));
    const res = await fetch(`/api/record/${captureId}/finish`, { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.detail || 'Error');
      return;
    }
    created = data.created;
    await loadMeetingsList();
    if (created && created[0]) await selectMeeting(created[0].id, true);
  } else {
    created = await handleFileSelect([new File([blob], safeName, { type: 'audio/webm' })]);
  }
  const savedPath = created && created[0] && created[0].media_path;
  showToast(
    t('recordSavedQueued'),
    savedPath ? { label: t('showInFolder'), title: savedPath, onClick: () => revealPath(savedPath) } : undefined
  );
}

// --- Video: shown automatically for recordings with a video track; "collapsed" moves the same
// <video> element into a small preview in the player bar instead of hiding it.
const VIDEO_CLASS_EXPANDED = 'w-full max-h-56 rounded-xl bg-black mx-auto cursor-pointer shadow-2xl border border-white/10';
const VIDEO_CLASS_MINI = 'w-full h-full object-cover pointer-events-none';
const VIDEO_ICONS = {
  collapse: '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25"/></svg>',
  expand: '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15"/></svg>',
};

function setVideoMode(mode) {
  state.videoMode = mode === 'mini' ? 'mini' : 'expanded';
  try { localStorage.setItem('verbascribe_video_mode', state.videoMode); } catch (e) {}
  applyVideoLayout();
}

function toggleVideoDrawer() {
  setVideoMode(state.videoMode === 'expanded' ? 'mini' : 'expanded');
}

function onVideoClick() {
  if (state.videoMode === 'mini') setVideoMode('expanded');
  else togglePlayPause();
}

function applyVideoLayout() {
  const player = document.getElementById('media-player');
  const drawer = document.getElementById('video-drawer');
  const expandedSlot = document.getElementById('video-expanded-slot');
  const miniSlot = document.getElementById('video-mini-slot');
  const btn = document.getElementById('video-drawer-btn');
  const pipBtn = document.getElementById('video-pip-btn');
  if (!player || !drawer) return;

  // In theatre mode the video stays in the theatre even while a new source (audio track) loads
  const mode = state.theatre ? 'theatre' : (state.hasVideo ? state.videoMode : 'none');
  drawer.classList.toggle('hidden', mode !== 'expanded');
  miniSlot.classList.toggle('hidden', mode !== 'mini');
  // Moving the element synchronously keeps playback running
  const target = mode === 'mini' ? miniSlot : mode === 'theatre' ? document.getElementById('theatre-video-slot') : expandedSlot;
  if (player.parentElement !== target) target.prepend(player);
  player.className = mode === 'mini' ? VIDEO_CLASS_MINI : mode === 'theatre' ? VIDEO_CLASS_THEATRE : VIDEO_CLASS_EXPANDED;
  const theatreBtn = document.getElementById('theatre-btn');
  theatreBtn.classList.toggle('hidden', !state.hasVideo && !state.theatre);
  theatreBtn.classList.toggle('ui-btn-active', !!state.theatre);
  theatreBtn.classList.toggle('ui-btn-subtle', !state.theatre);
  theatreBtn.title = t(state.theatre ? 'tipTheatreExit' : 'tipTheatre');

  btn.classList.toggle('hidden', !state.hasVideo || !!state.theatre);
  if (state.hasVideo) {
    const expanded = mode === 'expanded';
    btn.innerHTML = `${expanded ? VIDEO_ICONS.collapse : VIDEO_ICONS.expand}<span class="player-btn-label">${escapeHtml(t(expanded ? 'videoCollapse' : 'videoExpand'))}</span>`;
    btn.title = t(expanded ? 'tipVideoCollapse' : 'tipVideoExpand');
  }

  const inPip = !!document.pictureInPictureElement && document.pictureInPictureElement === player;
  pipBtn.classList.toggle('hidden', !state.hasVideo || !document.pictureInPictureEnabled);
  pipBtn.classList.toggle('ui-btn-active', inPip);
  pipBtn.classList.toggle('ui-btn-subtle', !inPip);
  pipBtn.title = t(inPip ? 'tipVideoPipExit' : 'tipVideoPip');
}

// --- Theatre mode: large video with the transcript beside or below it ---
// The video element, the transcript pane and the player bar are moved into #theatre and back, so
// playback, live highlighting and click-to-seek keep working unchanged.
const VIDEO_CLASS_THEATRE = 'max-w-full max-h-full w-full h-full object-contain bg-black cursor-pointer';
const theatre = { home: {} };

const THEATRE_LAYOUTS = ['side', 'below', 'subs'];

function theatreLayout() {
  try {
    const v = localStorage.getItem('verbascribe_theatre_layout');
    return THEATRE_LAYOUTS.includes(v) ? v : 'side';
  } catch (e) { return 'side'; }
}

function setTheatreLayout(layout) {
  try { localStorage.setItem('verbascribe_theatre_layout', THEATRE_LAYOUTS.includes(layout) ? layout : 'side'); } catch (e) {}
  applyTheatreLayout();
}

function applyTheatreLayout() {
  const layout = theatreLayout();
  const below = layout === 'below';
  const subs = layout === 'subs';
  document.getElementById('theatre-body').className = `flex-1 min-h-0 flex ${below ? 'flex-col' : 'flex-row'}`;
  document.getElementById('theatre-video-slot').className = subs
    ? 'flex-1 min-w-0 min-h-0 bg-black flex items-center justify-center relative'
    : `${below ? 'h-[58%] w-full' : 'flex-1 min-w-0'} min-h-0 bg-black flex items-center justify-center shrink-0 relative`;
  document.getElementById('theatre-text-slot').className = subs
    ? 'hidden'
    : `${below ? 'flex-1 w-full border-t' : 'w-[38%] min-w-[320px] max-w-[640px] border-l'} apple-border min-h-0 flex flex-col bg-apple-bg`;
  document.getElementById('theatre-caption').classList.toggle('hidden', !subs);
  const langCtrl = document.getElementById('tx-view-controls');
  if (subs && !theatre.home.txctrl) moveIntoTheatre('txctrl', langCtrl, document.getElementById('theatre-lang-slot'));
  if (!subs && theatre.home.txctrl) {
    moveBackFromTheatre('txctrl', langCtrl);
    renderTranslationControls();
  }
  if (subs) updateTheatreCaption(document.getElementById('media-player').currentTime || 0, true);
  THEATRE_LAYOUTS.forEach(k => {
    document.getElementById(`theatre-layout-${k}`).className =
      `apple-segment-btn text-[11px] ${k === layout ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  });
  redrawAfterLayoutChange();
}

// --- "Subtitles" layout: the current phrase over the video, in the language chosen for the text ---
// A long phrase is shown in parts of at most two lines: by word timings for the original, in
// proportion to length for a translation (it has no word timings).
const CAPTION_MAX_CHARS = 90;
const _caption = { key: '' };

function splitCaption(words) {
  const parts = [];
  let cur = [];
  let len = 0;
  for (const w of words) {
    const add = w.text.length + (cur.length ? 1 : 0);
    if (cur.length && len + add > CAPTION_MAX_CHARS) {
      parts.push(cur);
      cur = [];
      len = 0;
    }
    cur.push(w);
    len += w.text.length + (cur.length > 1 ? 1 : 0);
  }
  if (cur.length) parts.push(cur);
  return parts;
}

// Words of a text with estimated times inside [start, end], by their length
function timedWordsByLength(text, start, end) {
  const tokens = (text || '').split(/\s+/).filter(Boolean);
  const total = tokens.reduce((a, w) => a + w.length + 1, 0) || 1;
  let acc = 0;
  return tokens.map(w => {
    const s = start + (end - start) * acc / total;
    acc += w.length + 1;
    return { text: w, start: s, end: start + (end - start) * acc / total };
  });
}

function captionPart(words, cur) {
  const parts = splitCaption(words);
  if (!parts.length) return '';
  let idx = parts.findIndex(p => cur < p[p.length - 1].end);
  if (idx < 0) idx = parts.length - 1;
  // Whisper words carry their own leading space (" какой", "-то"); estimated words do not
  return parts[idx].map(w => (w.raw !== undefined ? w.raw : ` ${w.text}`)).join('').trim();
}

function updateTheatreCaption(cur, force) {
  const box = document.getElementById('theatre-caption');
  if (!box || !state.theatre || theatreLayout() !== 'subs') return;
  const m = state.activeMeeting;
  const segs = (m && m.segments) || [];
  // The phrase being spoken, or nothing in a pause (a short grace keeps a caption from flickering)
  let lo = 0, hi = segs.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (Number(segs[mid].start) <= cur) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  const seg = found >= 0 && cur <= Number(segs[found].end) + 0.4 ? segs[found] : null;
  let main = '';
  let second = '';
  if (seg) {
    const start = Number(seg.start), end = Number(seg.end);
    const words = (seg.words || []).length
      ? seg.words.map(w => ({ text: String(w.word).trim(), raw: String(w.word), start: Number(w.start), end: Number(w.end) })).filter(w => w.text)
      : timedWordsByLength(seg.text, start, end);
    const orig = captionPart(words, cur);
    const tr = activeSegmentTranslation(seg);
    const trText = tr && tr.text ? captionPart(timedWordsByLength(tr.text, start, end), cur) : '';
    if (state.txView === 'tr' && trText) main = trText;
    else if (state.txView === 'both' && trText) { main = orig; second = trText; }
    else main = orig;
  }
  const key = `${main}\u0000${second}`;
  if (!force && key === _caption.key) return;
  _caption.key = key;
  box.innerHTML = main
    ? `<span class="theatre-caption-line">${escapeHtml(main)}</span>`
      + (second ? `<span class="theatre-caption-line theatre-caption-second">${escapeHtml(second)}</span>` : '')
    : '';
  box.classList.toggle('empty', !main);
}

function redrawAfterLayoutChange() {
  requestAnimationFrame(() => {
    const player = document.getElementById('media-player');
    const dur = player.duration || (state.activeMeeting && state.activeMeeting.duration) || 1;
    renderPlayerWaveform((player.currentTime || 0) / dur);
    const active = _playbackIndex.activeSeg;
    if (active) active.scrollIntoView({ block: 'center' });
  });
}

// Moves `el` into `target`, leaving a placeholder so it can go back to exactly the same place
function moveIntoTheatre(key, el, target) {
  const marker = document.createComment(`theatre:${key}`);
  el.parentNode.insertBefore(marker, el);
  theatre.home[key] = { marker, wasHidden: el.classList.contains('hidden') };
  target.appendChild(el);
}

function moveBackFromTheatre(key, el) {
  const home = theatre.home[key];
  if (!home) return;
  home.marker.parentNode.insertBefore(el, home.marker);
  home.marker.remove();
  el.classList.toggle('hidden', home.wasHidden);
  delete theatre.home[key];
}

function toggleTheatre() {
  if (state.theatre) exitTheatre();
  else enterTheatre();
}

function enterTheatre() {
  if (state.theatre || !state.hasVideo || !state.activeMeeting) return;
  state.theatre = true;
  if (document.pictureInPictureElement) document.exitPictureInPicture().catch(() => {});
  document.getElementById('theatre-title').textContent = state.activeMeeting.title || '';
  const pane = document.getElementById('wview-transcript');
  moveIntoTheatre('pane', pane, document.getElementById('theatre-text-slot'));
  pane.classList.remove('hidden');
  moveIntoTheatre('footer', document.getElementById('player-footer'), document.getElementById('theatre-footer-slot'));
  document.getElementById('theatre').classList.remove('hidden');
  applyVideoLayout();
  applyTheatreLayout();
}

function exitTheatre() {
  if (!state.theatre) return;
  state.theatre = false;
  if (isTheatreFullscreen()) setTheatreFullscreen(false);
  document.getElementById('theatre').classList.add('hidden');
  if (theatre.home.txctrl) {
    moveBackFromTheatre('txctrl', document.getElementById('tx-view-controls'));
    renderTranslationControls();
  }
  moveBackFromTheatre('footer', document.getElementById('player-footer'));
  moveBackFromTheatre('pane', document.getElementById('wview-transcript'));
  // The workspace tab may have changed meanwhile; the pane follows the current tab
  document.getElementById('wview-transcript').classList.toggle('hidden', state.activeWorkspaceTab !== 'transcript');
  applyVideoLayout();
  redrawAfterLayoutChange();
}

// The desktop window (pywebview) does not support the browser Fullscreen API: there the native
// window is toggled through its JS bridge. In a regular browser the Fullscreen API is used.
state.windowFullscreen = false;

function nativeWindowApi() {
  const api = window.pywebview && window.pywebview.api;
  return api && api.toggle_fullscreen ? api : null;
}

function isTheatreFullscreen() {
  return nativeWindowApi() ? state.windowFullscreen : !!document.fullscreenElement;
}

function updateTheatreFullscreenButton() {
  const btn = document.getElementById('theatre-fs-btn');
  if (btn) btn.textContent = t(isTheatreFullscreen() ? 'theatreFullscreenExit' : 'theatreFullscreen');
  if (state.theatre) redrawAfterLayoutChange();
}

async function setTheatreFullscreen(on) {
  const api = nativeWindowApi();
  try {
    if (api) {
      if (on !== state.windowFullscreen) state.windowFullscreen = !!(await api.toggle_fullscreen());
    } else if (on && !document.fullscreenElement) {
      await document.getElementById('theatre').requestFullscreen();
    } else if (!on && document.fullscreenElement) {
      await document.exitFullscreen();
    }
  } catch (e) {
    console.error(e);
  }
  updateTheatreFullscreenButton();
}

function toggleTheatreFullscreen() {
  return setTheatreFullscreen(!isTheatreFullscreen());
}

document.addEventListener('fullscreenchange', updateTheatreFullscreenButton);

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !state.theatre) return;
  // Esc first closes an open dialog, menu or inline editor; the browser handles its own fullscreen
  const modalOpen = [...document.querySelectorAll('[id^="modal-"]')].some(m => !m.classList.contains('hidden'));
  const editing = e.target && (e.target.tagName === 'TEXTAREA' || (e.target.tagName === 'INPUT' && e.target.value));
  if (modalOpen || editing || document.fullscreenElement) return;
  e.preventDefault();
  // In the desktop window Esc first leaves full screen, then the theatre
  if (nativeWindowApi() && state.windowFullscreen) setTheatreFullscreen(false);
  else exitTheatre();
});

async function toggleVideoPip() {
  const player = document.getElementById('media-player');
  try {
    if (document.pictureInPictureElement) await document.exitPictureInPicture();
    else await player.requestPictureInPicture();
  } catch (e) {
    console.error(e);
    showToast(t('videoPipFailed'));
  }
}

function toggleAutoScroll() {
  state.autoScroll = state.autoScroll === false ? true : false;
  const btn = document.getElementById('autoscroll-btn');
  if (btn) {
    btn.className = state.autoScroll
      ? 'ui-btn ui-btn-active'
      : 'ui-btn ui-btn-subtle';
  }
}

// --- Keyboard shortcuts (listed in the "?" window). Codes, not letters: they work in any layout ---
function isTypingTarget(el) {
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));
}

function anyModalOpen() {
  return [...document.querySelectorAll('[id^="modal-"]')].some(m => !m.classList.contains('hidden'));
}

function focusTranscriptSearch() {
  if (!state.activeMeeting) {
    document.getElementById('global-search-input')?.focus();
    return;
  }
  if (state.activeWorkspaceTab !== 'transcript' && !state.theatre) switchWorkspaceTab('transcript');
  const input = document.getElementById('transcript-search');
  input.focus();
  input.select();
}

document.addEventListener('keydown', (e) => {
  if (e.defaultPrevented || e.altKey || e.metaKey) return;
  if (e.key === 'Escape') {
    // Closes the dialog on top; the recording window stays open while it records
    const open = [...document.querySelectorAll('[id^="modal-"]')].filter(m => !m.classList.contains('hidden'));
    const top = open[open.length - 1];
    if (top && top.id !== 'modal-record' && !isTypingTarget(e.target)) {
      e.preventDefault();
      closeModal(top.id.slice('modal-'.length));
    }
    return;
  }
  if (e.ctrlKey) {
    if (e.code === 'KeyO' && !e.shiftKey) {
      e.preventDefault();
      openModal('import');
    } else if (e.code === 'KeyF' && !e.shiftKey) {
      e.preventDefault();
      focusTranscriptSearch();
    }
    return;
  }
  if (isTypingTarget(e.target) || anyModalOpen()) return;
  const player = document.getElementById('media-player');
  const hasMedia = !!state.activeMeeting && !!player && !!player.src;
  if (e.key === '?' || (e.code === 'Slash' && e.shiftKey)) {
    e.preventDefault();
    openModal('shortcuts');
  } else if (e.code === 'Slash') {
    e.preventDefault();
    focusTranscriptSearch();
  } else if (e.code === 'Space' && hasMedia) {
    e.preventDefault();
    document.activeElement?.blur?.();
    togglePlayPause();
  } else if ((e.code === 'ArrowLeft' || e.code === 'ArrowRight') && hasMedia) {
    e.preventDefault();
    const step = e.shiftKey ? 15 : 5;
    skipPlayer(e.code === 'ArrowLeft' ? -step : step);
  } else if (e.code === 'KeyF' && !e.shiftKey && state.hasVideo) {
    e.preventDefault();
    toggleTheatre();
  } else if (e.code === 'KeyR' && !e.shiftKey) {
    e.preventDefault();
    openRecordModal();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.altKey && e.code === 'Space') {
    e.preventDefault();
    togglePlayPause();
  } else if (e.altKey && e.code === 'ArrowLeft') {
    e.preventDefault();
    skipPlayer(-5);
  } else if (e.altKey && e.code === 'ArrowRight') {
    e.preventDefault();
    skipPlayer(5);
  }
});

// --- Easter egg: willow fluff from the logo ---
function releaseWillowFluff(count, spread) {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const logo = document.getElementById('app-logo');
  if (!logo) return;
  const r = logo.getBoundingClientRect();
  for (let i = 0; i < count; i++) {
    const el = document.createElement('span');
    el.className = 'willow-fluff';
    el.style.left = `${r.left + r.width * (0.35 + Math.random() * 0.5)}px`;
    el.style.top = `${r.top + r.height * (0.15 + Math.random() * 0.5)}px`;
    // Drift right and up like fluff in a breeze, wider for the burst
    el.style.setProperty('--x', `${(30 + Math.random() * 90) * spread}px`);
    el.style.setProperty('--y', `${(-20 - Math.random() * 70) * spread + Math.random() * 40 * spread}px`);
    el.style.setProperty('--r', `${Math.round(Math.random() * 360)}deg`);
    el.style.setProperty('--t', `${(2.2 + Math.random() * 1.8) * (spread > 1 ? 1.4 : 1)}s`);
    el.style.animationDelay = `${Math.random() * (spread > 1 ? 700 : 250)}ms`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 6500);
  }
}

function initLogoEasterEgg() {
  const logo = document.getElementById('app-logo');
  if (!logo) return;
  let lastHover = 0;
  logo.addEventListener('mouseenter', () => {
    const now = Date.now();
    if (now - lastHover > 1200) releaseWillowFluff(6, 1);
    lastHover = now;
  });
  let clicks = [];
  logo.addEventListener('click', () => {
    const now = Date.now();
    clicks = clicks.filter(t => now - t < 1600).concat(now);
    if (clicks.length >= 5) {
      clicks = [];
      releaseWillowFluff(48, 3.2);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  state.autoScroll = true;
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
  loadDictionary();
  loadVram();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) scheduleVramRefresh(100); });
  startLiveUpdates();
  loadQueue().catch(console.error);
  pollMeetingsLoop();
});

// --- Summaries ---
function selectSummaryTab(tab) {
  state.activeSummaryTab = tab;
  ['protocol', 'tasks', 'tldr', 'chapters'].forEach(k => {
    const btn = document.getElementById(`sumtab-${k}`);
    if (btn) {
      btn.className = (k === tab)
        ? 'apple-segment-btn apple-segment-active'
        : 'apple-segment-btn text-apple-secondary hover:text-white';
    }
  });
  renderSummaryPanel();
}

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
    `<button onclick="seekPlayer(${c.start})" id="chapter-${i}" title="${escapeHtml(c.desc || c.title)}"
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

function renderSummaryPanel() {
  const box = document.getElementById('summary-content');
  if (!state.activeMeeting) return;
  const summaries = state.activeMeeting.summaries || {};
  const md = summaries[state.activeSummaryTab] || '';

  if (!md) {
    box.innerHTML = `<div class="py-12 text-center text-xs text-apple-secondary">
      ${t('emptySummary')}
      ${summaries.error ? `<div class="mt-2 text-apple-red">${escapeHtml(summaries.error)}</div>` : ''}
    </div>`;
    return;
  }

  // LLM output is derived from the recording's speech, so it is treated as untrusted HTML
  let html = DOMPurify.sanitize(marked.parse(md), { FORBID_TAGS: ['style', 'form', 'input'], FORBID_ATTR: ['style'] });
  html = html.replace(/\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g, (match, p1, p2, p3) => {
    const secs = p3 ? (Number(p1)*3600 + Number(p2)*60 + Number(p3)) : (Number(p1)*60 + Number(p2));
    return `<button onclick="seekPlayer(${secs})" title="${escapeHtml(t('tipSeekTimestamp'))}" class="text-apple-blue hover:underline font-mono text-xs">[${p1}:${p2}${p3 ? ':'+p3 : ''}]</button>`;
  });
  box.innerHTML = html;
}

async function generateSummaryForActiveTab() {
  if (!state.activeMeeting) return;
  const btn = document.getElementById('generate-summary-btn');
  const box = document.getElementById('summary-content');
  const chosenModel = document.getElementById('summary-llm-select')?.value || undefined;
  const prettyModel = formatModelName(chosenModel || 'Ollama');
  btn.disabled = true;
  box.innerHTML = `<div class="py-12 flex items-center justify-center gap-2 text-xs text-apple-secondary animate-pulse"><span>${state.uiLang === 'en' ? 'Generating analysis' : 'Формирование анализа'}</span><span class="px-2 py-0.5 rounded-md bg-white/10 text-white font-medium">${escapeHtml(prettyModel)}</span></div>`;

  try {
    const res = await fetch(`/api/meetings/${state.activeMeeting.id}/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: state.activeSummaryTab, model: chosenModel, lang: state.uiLang })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error');
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

  const chosenModel = document.getElementById('summary-llm-select')?.value || undefined;
  const prettyModel = formatModelName(chosenModel || 'Ollama');
  const box = document.getElementById('summary-content');
  box.innerHTML = `<div class="py-12 flex items-center justify-center gap-2 text-xs text-apple-secondary animate-pulse"><span>${state.uiLang === 'en' ? 'Searching for answer' : 'Поиск ответа'}</span><span class="px-2 py-0.5 rounded-md bg-white/10 text-white font-medium">${escapeHtml(prettyModel)}</span></div>`;

  try {
    const res = await fetch(`/api/meetings/${state.activeMeeting.id}/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: 'custom', custom_prompt: prompt, model: chosenModel, lang: state.uiLang })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error');
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
  const text = (state.activeMeeting.summaries || {})[state.activeSummaryTab] || '';
  if (!text) return;
  navigator.clipboard.writeText(text);
  showToast(state.uiLang === 'en' ? 'Copied' : 'Скопировано');
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
    const data = await res.json();
    el.textContent = data.folder || '';
    el.title = data.folder || '';
  } catch (e) {}
}

async function pickExportDir() {
  try {
    const res = await fetch('/api/export-dir/pick', { method: 'POST' });
    const data = await res.json();
    if (data && data.ok) refreshExportDirLabel();
  } catch (e) {}
}

function openExportDir() {
  const el = document.getElementById('set-export-dir');
  if (el && el.textContent) revealPath(el.textContent);
}

async function exportCurrentMeeting(fmt, opts) {
  document.getElementById('export-menu')?.classList.add('hidden');
  if (!state.activeMeeting) {
    showToast(state.uiLang === 'en' ? 'Select a recording first' : 'Сначала выберите запись');
    return;
  }
  await exportMeetingById(state.activeMeeting.id, fmt, opts);
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
