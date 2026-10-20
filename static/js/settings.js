// VerbaScribe page script, part 3 of 9: Settings: system info, FFmpeg, models in VRAM, Whisper and Ollama models, cloud LLM, search by meaning, dictionary.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

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
      ? hw.device_name.replace(/^NVIDIA (GeForce )?/, '')
      : t('hwNoGpu');
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
    syncVoiceProfilesToggle();
    document.getElementById('set-update-check').checked = s.update_check !== 'false';
    document.getElementById('set-vad').checked = s.vad_filter !== 'false';
    if (document.getElementById('set-audio-enhance')) {
      document.getElementById('set-audio-enhance').checked = s.audio_enhance !== 'false';
    }
    if (document.getElementById('set-clean-fillers')) {
      document.getElementById('set-clean-fillers').checked = s.clean_fillers !== 'false';
    }
    document.getElementById('set-ollama-model').value = s.ollama_model || 'qwen2.5:7b';
    document.getElementById('set-auto-summary').checked = s.auto_summary !== 'false';
    document.getElementById('set-auto-title').checked = s.auto_title !== 'false';
    document.getElementById('set-quiet-mode').checked = s.quiet_mode === 'true';
    document.getElementById('set-semantic').checked = s.semantic_search === 'true';
    document.getElementById('set-semantic-model').value = s.semantic_model || 'bge-m3';
    loadSemanticStatus();
    const idleSel = document.getElementById('set-idle-unload');
    idleSel.value = [...idleSel.options].some(o => o.value === String(s.idle_unload_minutes)) ? String(s.idle_unload_minutes) : '10';
    fillObsidianSettings(s);
    fillDubSettings(s);
    renderRecordSaveHint();
    state.liveModelSetting = s.live_whisper_model || 'auto';
    document.getElementById('set-record-hotkey').value = s.record_hotkey ?? 'Ctrl+Alt+R';
    fillCallSettings(s);
    loadHotkeyStatus();
    renderLiveModelSelect();

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
  if (c.kind === 'live') return `${t('vramChipLive')} · ${c.label || c.name}`;
  if (c.kind === 'ollama') return `${c.label || c.name}${c.size_gb ? ' · ' + c.size_gb + ' ' + t('unitGB') : ''}`;
  return c.label || c.name;
}

function vramChipTitle(c) {
  if (c.kind === 'dub') return t('tipVramDub');
  if (c.loading) return t('tipVramLoading').replace('{name}', vramChipLabel(c));
  if (c.kind === 'live') return c.can_unload ? t('tipVramUnload').replace('{name}', vramChipLabel(c)) : t('tipVramInUse');
  if (c.loaded) return c.can_unload ? t('tipVramUnload').replace('{name}', vramChipLabel(c)) : t('tipVramInUse');
  return c.can_preload ? t('tipVramPreload').replace('{name}', vramChipLabel(c)) : t('tipVramNotLoaded');
}

function renderSystemLoad(sys) {
  const row = document.getElementById('sys-row');
  if (!row) return;
  const cpu = sys && sys.cpu_percent;
  const ram = sys && sys.ram;
  row.classList.toggle('hidden', cpu == null && !ram);
  const nameEl = document.getElementById('cpu-name');
  if (sys && sys.cpu_name && nameEl.textContent !== sys.cpu_name) {
    nameEl.textContent = sys.cpu_name;
    document.getElementById('cpu-cell').title = `${sys.cpu_name}${sys.cores ? ` · ${t('hwThreads').replace('{n}', sys.cores)}` : ''}\n${t('tipCpuUsage')}`;
  }
  if (cpu != null) {
    document.getElementById('cpu-text').textContent = `${Math.round(cpu)}%`;
    document.getElementById('cpu-bar').style.width = `${Math.min(100, cpu)}%`;
    document.getElementById('cpu-bar').className = `${cpu > 85 ? 'bg-apple-red/80' : 'bg-apple-green/70'}`;
  }
  if (ram) {
    const pct = (100 * ram.used_gb) / Math.max(0.1, ram.total_gb);
    document.getElementById('ram-text').textContent = `${ram.used_gb}/${ram.total_gb} ${t('unitGB')}`;
    document.getElementById('ram-name').textContent = t('hwRamName').replace('{gb}', Math.round(ram.total_gb));
    document.getElementById('ram-bar').style.width = `${Math.min(100, pct)}%`;
    document.getElementById('ram-bar').className = `${pct > 90 ? 'bg-apple-red/80' : 'bg-apple-amber/70'}`;
    document.getElementById('ram-cell').title = t('tipRamUsage').replace('{free}', ram.free_gb);
  }
}

function renderIdleStatus(idle) {
  const el = document.getElementById('idle-unload-status');
  if (!el || !idle) return;
  const loaded = (vram.chips || []).some(c => c.loaded && c.kind !== 'dub');
  el.textContent = idle.minutes && loaded && idle.unload_in_sec != null
    ? t('idleUnloadIn').replace('{min}', Math.max(1, Math.ceil(idle.unload_in_sec / 60))) : '';
}

function renderVram(data) {
  vram.data = data;
  renderSystemLoad(data.system);
  const row = document.getElementById('vram-row');
  const gpu = data.gpu;
  row.classList.toggle('hidden', !gpu);
  document.querySelector('#hw-block .hw-row-btn .hw-bar').classList.toggle('invisible', !gpu);
  if (!gpu) { document.getElementById('vram-text').textContent = ''; vram.chips = data.chips || []; renderIdleStatus(data.idle); return; }
  document.getElementById('vram-text').textContent = `${gpu.used_gb}/${gpu.total_gb} ${t('unitGB')}`;
  document.getElementById('vram-bar').style.width = `${Math.min(100, (100 * gpu.used_gb) / Math.max(1, gpu.total_gb))}%`;
  document.querySelector('#hw-block .hw-row-btn').title = `${t('tipVramUsage').replace('{free}', gpu.free_gb)}\n${t('tipHwStatus')}`;

  const chips = data.chips || [];  // every model: loaded, loading or ready to load
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
      <span class="truncate">${c.default ? '★ ' : ''}${escapeHtml(vramChipLabel(c))}</span>${icon}</button>`;
  }).join('');
  // Unchanged chips are left alone, so a refresh never swallows a click
  const box = document.getElementById('vram-chips');
  if (html !== vram.html) {
    box.innerHTML = html;
    // The recording studio covers the sidebar: it shows the same chips in its header
    const studioBox = document.getElementById('studio-vram-chips');
    if (studioBox) studioBox.innerHTML = html;
    vram.html = html;
  }
  vram.chips = chips;
  renderIdleStatus(data.idle);
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
  state.whisperInstalled = whisperList.filter(i => i.installed).map(i => i.id);
  renderLiveModelSelect();

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

  state.lastOllama = ollama;
  renderSummaryModelSelect();
  renderSemanticModelCard();

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

// The model choice above the analysis: the cloud model (when set up) and the Ollama models
// Before the analysis runs: a cloud request shows its model and its size in tokens; a recording
// marked "local analysis only" says it stays on this computer
let _estimateSeq = 0;
async function renderAnalysisEstimate() {
  const box = document.getElementById('analysis-estimate');
  const m = state.activeMeeting;
  if (!box) return;
  if (!m || !(m.segments || []).length) { box.classList.add('hidden'); return; }
  const seq = ++_estimateSeq;
  const est = await fetch(`/api/meetings/${m.id}/llm-estimate`).then(r => r.ok ? r.json() : null).catch(() => null);
  if (seq !== _estimateSeq || !est || !state.activeMeeting || state.activeMeeting.id !== m.id) return;
  const cloudSelected = !!(state.llm && state.llm.mode === 'cloud');
  let html = '';
  if (est.local_only) {
    html = `<span class="text-apple-green">${ICON_LOCK}</span><span class="text-apple-secondary">${escapeHtml(cloudSelected ? t('estLocalOnlyCloud') : t('estLocalOnly'))}</span>`;
  } else if (est.cloud) {
    const tokens = est.tokens >= 1000 ? `${(est.tokens / 1000).toFixed(est.tokens >= 10000 ? 0 : 1)} ${t('estThousand')}` : String(est.tokens);
    html = `<span class="text-apple-amber">☁</span><span class="text-apple-secondary">${escapeHtml(t('estCloud').replace('{model}', est.label || '').replace('{tokens}', tokens))}</span>`;
  }
  box.innerHTML = html;
  box.classList.toggle('hidden', !html);
}

const ICON_LOCK = '<svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" stroke-width="1.9" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"/></svg>';

// --- Calls recorded by themselves (app/call_watch.py) ---
const CALL_APP_NAMES = { zoom: 'Zoom', teams: 'Microsoft Teams', telegram: 'Telegram', discord: 'Discord', skype: 'Skype', whatsapp: 'WhatsApp' };

async function fillCallSettings(s) {
  const st = await fetch('/api/calls/status').then(r => r.json()).catch(() => ({ supported: false }));
  document.getElementById('call-record-block').classList.toggle('hidden', !st.supported);
  document.getElementById('set-call-record').checked = s.call_auto_record === 'true';
  const chosen = new Set((s.call_apps || 'zoom,teams,telegram').split(','));
  document.getElementById('call-apps').innerHTML = Object.entries(CALL_APP_NAMES).map(([id, name]) =>
    `<label class="flex items-center gap-1.5 text-[11.5px] text-apple-label cursor-pointer"><input type="checkbox" data-call-app="${id}" ${chosen.has(id) ? 'checked' : ''} class="rounded bg-apple-sidebar border-white/20 text-apple-blue" />${escapeHtml(name)}</label>`).join('');
  const sel = document.getElementById('set-call-preset');
  sel.innerHTML = recordPresets().map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(presetLabel(p))}</option>`).join('');
  sel.value = [...sel.options].some(o => o.value === s.call_preset) ? s.call_preset : (sel.options[0] || {}).value || '';
  renderCallSettings();
}

// What uses the microphone now, refreshed while the settings are open: during a call it shows
// whether Windows reports the app at all (if not, it cannot be detected)
async function refreshCallMicNow() {
  const box = document.getElementById('call-mic-now');
  const modal = document.getElementById('modal-settings');
  const block = document.getElementById('call-record-block');
  // Only while the recording settings are on screen
  if (!box || !modal || modal.classList.contains('hidden') || !block || block.classList.contains('hidden') || !block.offsetParent) return;
  const st = await fetch('/api/calls/status').then(r => r.json()).catch(() => null);
  if (st) {
    const names = (st.mic_now || []).map(n => n.replace(/_[a-z0-9]{13}$/, ''));
    box.textContent = `${t('callMicNow')}: ${names.length ? names.join(', ') : t('callMicNobody')}`
      + (st.active ? ` · ${t('callActive').replace('{app}', st.active)}` : '');
  }
}

setInterval(refreshCallMicNow, 3000);

function renderCallSettings() {
  document.getElementById('call-record-options').classList.toggle('opacity-50', !document.getElementById('set-call-record').checked);
}

function callSettingsPayload() {
  const box = document.getElementById('call-apps');
  if (!box || !box.children.length) return {};
  return {
    call_auto_record: String(document.getElementById('set-call-record').checked),
    call_apps: [...box.querySelectorAll('input[data-call-app]:checked')].map(i => i.dataset.callApp).join(','),
    call_preset: document.getElementById('set-call-preset').value || 'call',
  };
}

// --- Recording hotkey: pressed in the field, registered system-wide by the tray (app/tray.py) ---
function captureHotkey(e) {
  e.preventDefault();
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return;
  if (e.key === 'Escape') { e.target.blur(); return; }
  if (e.key === 'Backspace' || e.key === 'Delete') { e.target.value = ''; return; }
  const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Win'].filter(Boolean);
  if (!mods.some(m => m !== 'Shift')) {
    document.getElementById('hotkey-status').textContent = t('hotkeyNeedsMod');
    return;
  }
  // Codes, not letters: the same keys work in any keyboard layout
  const code = e.code.startsWith('Key') ? e.code.slice(3) : e.code.startsWith('Digit') ? e.code.slice(5)
    : /^F\d{1,2}$/.test(e.code) ? e.code : { Space: 'Space', Pause: 'Pause', Insert: 'Insert', Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown' }[e.code];
  if (!code) return;
  e.target.value = [...mods, code].join('+');
  document.getElementById('hotkey-status').textContent = t('hotkeySaveHint');
}

function clearHotkey() {
  document.getElementById('set-record-hotkey').value = '';
  document.getElementById('hotkey-status').textContent = t('hotkeySaveHint');
}

async function loadHotkeyStatus() {
  const box = document.getElementById('hotkey-status');
  const st = await fetch('/api/hotkey').then(r => r.json()).catch(() => null);
  if (!box || !st || st.ok === null || st.ok === undefined) { if (box) box.textContent = ''; return; }
  box.textContent = st.ok ? t('hotkeyActive') : (st.error || t('hotkeyFailed'));
  box.className = 'text-[11px] ' + (st.ok ? 'text-apple-green' : 'text-apple-amber');
}

// Captions while recording: "auto" or one of the downloaded Whisper models
function renderLiveModelSelect() {
  const sel = document.getElementById('set-live-model');
  if (!sel) return;
  const value = sel.dataset.touched ? sel.value : (state.liveModelSetting || 'auto');
  const ids = state.whisperInstalled || [];
  sel.innerHTML = `<option value="auto">${escapeHtml(t('liveModelAuto'))}</option>`
    + ids.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(formatModelName(id))}</option>`).join('');
  sel.value = [...sel.options].some(o => o.value === value) ? value : 'auto';
  sel.onchange = () => { sel.dataset.touched = '1'; };
}

// --- Search by meaning: status of the index, model download, building the index ---
async function loadSemanticStatus() {
  const box = document.getElementById('semantic-status');
  if (!box) return;
  const st = await fetch('/api/semantic/status').then(r => r.json()).catch(() => null);
  if (!st) return;
  const parts = [t('semanticIndexed').replace('{n}', st.indexed).replace('{total}', st.total)];
  if (st.running) parts.push(t('semanticRunning'));
  box.textContent = parts.join(' · ');
  box.className = 'pl-6 text-[11px] ' + (st.error ? 'text-apple-amber' : 'text-apple-secondary');
  if (st.error) box.textContent += ` · ${st.error}`;
  clearTimeout(window._semanticTimer);
  const modal = document.getElementById('modal-settings');
  if (st.running && modal && !modal.classList.contains('hidden')) window._semanticTimer = setTimeout(loadSemanticStatus, 3000);
}

// The embedding model as a card like the other models: size, what it is, download or build
function renderSemanticModelCard() {
  const box = document.getElementById('semantic-model-card');
  const ollama = state.lastOllama;
  if (!box) return;
  const model = document.getElementById('set-semantic-model').value || 'bge-m3';
  const installed = !!ollama && (ollama.models || []).some(n => n === model || n.startsWith(model + ':'));
  const ps = (ollama && ollama.pull_state) || {};
  const pulling = ps.active && ps.model === model;
  const action = pulling
    ? `<span class="ui-btn bg-apple-blue/20 text-apple-blue font-mono animate-pulse">${ps.progress || 1}%</span>`
    : installed
      ? `<button onclick="buildSemanticIndex()" class="ui-btn ui-btn-subtle text-white hover:bg-apple-blue">${escapeHtml(t('semanticBuild'))}</button>`
      : `<button onclick="pullSemanticModel()" class="ui-btn ui-btn-primary">${SVG_ICONS.download}<span>${escapeHtml(t('semanticDownload'))} (1.2 GB)</span></button>`;
  box.innerHTML = `<div class="p-3 rounded-xl border transition flex items-center justify-between gap-3 ${installed ? 'bg-apple-blue/10 border-apple-blue/50' : 'bg-apple-sidebar border-white/10'}">
    <div class="min-w-0 flex-1">
      <div class="flex items-center flex-wrap gap-2">
        <span class="text-xs font-semibold text-white">BGE-M3</span>
        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">1.2 GB · MIT</span>
        <span class="text-[10px] text-apple-tertiary">${installed ? '✓ ' + escapeHtml(t('semanticInstalled')) : '1+ GB VRAM / CPU'}</span>
      </div>
      <p class="text-[11.5px] text-apple-secondary mt-1 leading-snug">${escapeHtml(t('semanticModelDesc'))}</p>
    </div>
    <div class="shrink-0">${action}</div>
  </div>`;
}

async function pullSemanticModel() {
  await pullOllamaModel(document.getElementById('set-semantic-model').value.trim() || 'bge-m3', false);
  renderSemanticModelCard();
}

async function buildSemanticIndex() {
  const enabled = document.getElementById('set-semantic');
  if (!enabled.checked) enabled.checked = true;
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: { semantic_search: 'true', semantic_model: document.getElementById('set-semantic-model').value.trim() || 'bge-m3' } }),
  });
  const res = await fetch('/api/semantic/index', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) showToast(data.detail || 'Error');
  setTimeout(loadSemanticStatus, 800);
}

function renderSummaryModelSelect() {
  const summarySelect = document.getElementById('summary-llm-select');
  const ollama = state.lastOllama;
  if (!summarySelect || !ollama || !ollama.catalog) return;
  const activeModel = document.getElementById('set-ollama-model').value || ollama.active_model || 'qwen2.5:7b';
  const cloud = cloudLlmChoice();
  const onCloud = !!cloud && state.llm.mode === 'cloud';
  summarySelect.innerHTML = (cloud
    ? `<option value="__cloud" ${onCloud ? 'selected' : ''}>☁ ${escapeHtml(cloud.label)}</option>` : '')
    + ollama.catalog.map(item => {
      const pretty = formatModelName(item.id);
      const mark = item.installed ? '● ' : '';
      return `<option value="${escapeHtml(item.id)}" ${!onCloud && item.id === activeModel ? 'selected' : ''}>${mark}${escapeHtml(pretty)}</option>`;
    }).join('');
  summarySelect.title = onCloud ? t('tipSummaryCloud') : t('tipSelectLlm');
}

// The configured cloud model, if it can be used (a key is saved and sending was agreed to)
function cloudLlmChoice() {
  const st = state.llm;
  if (!st || !st.consent) return null;
  const p = st.providers[st.provider];
  if (!p || (!p.key.saved && st.mode !== 'cloud')) return null;
  const name = p.name.replace(/ \(.*\)$/, '');
  // Chosen but not set up yet: still offered, so the request explains what is missing
  return { provider: st.provider, model: p.model, label: p.key.saved && p.model ? `${name} · ${p.model}` : `${name} · ${t('llmNotConfigured')}` };
}

async function loadLlmStatus() {
  try {
    const res = await fetch('/api/llm/status');
    if (res.ok) state.llm = await res.json();
  } catch (e) {
    console.error(e);
  }
  renderSummaryModelSelect();
  renderLlmSettings();
}

function renderLlmSettings() {
  const st = state.llm;
  if (!st || !document.getElementById('llm-cloud-panel')) return;
  const cloud = st.mode === 'cloud';
  for (const k of ['ollama', 'cloud']) {
    document.getElementById(`llm-mode-${k}`).className =
      `apple-segment-btn ${(k === 'cloud') === cloud ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  }
  document.getElementById('llm-cloud-panel').classList.toggle('hidden', !cloud && !state.llmShowCloud);
  const sel = document.getElementById('set-llm-provider');
  if (!state.llmProviderEdited) sel.value = st.provider;
  renderLlmProvider(true);
}

function renderLlmProvider(keepModel) {
  const st = state.llm;
  if (!st) return;
  const prov = document.getElementById('set-llm-provider').value;
  if (keepModel !== true) state.llmProviderEdited = true;
  const p = st.providers[prov];
  const modelInput = document.getElementById('set-llm-model');
  if (keepModel !== true || modelInput.dataset.prov !== prov) {
    modelInput.value = p.model || '';
    modelInput.dataset.prov = prov;
  }
  renderLlmModelSelect(prov);
  document.getElementById('llm-base-url-row').classList.toggle('hidden', prov !== 'openai_compat');
  document.getElementById('set-llm-base-url').value = prov === 'openai_compat' ? (p.base_url || '') : '';

  // Key: a clear "saved" mark, and where to get one (a link that opens in the browser)
  const keyInput = document.getElementById('set-llm-key');
  const badge = document.getElementById('llm-key-badge');
  const ok = p.key.saved && p.key.readable;
  badge.classList.toggle('hidden', !p.key.saved);
  badge.className = `${p.key.saved ? '' : 'hidden '}inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-medium ${ok ? 'bg-apple-green/15 text-apple-green' : 'bg-apple-red/15 text-apple-red'}`;
  badge.innerHTML = ok ? `${SVG_ICONS.check}<span>${escapeHtml(t('llmKeyBadge').replace('{last4}', p.key.last4))}</span>` : escapeHtml(t('llmKeyBadgeBroken'));
  keyInput.placeholder = p.key.saved ? t('llmKeySavedPh').replace('{last4}', p.key.last4) : t('llmKeyPh');
  keyInput.classList.toggle('border-apple-green/50', ok);
  const hint = document.getElementById('llm-key-hint');
  hint.textContent = p.key.saved
    ? (p.key.readable ? t('llmKeySaved') : t('llmKeyUnreadable'))
    : (p.keys_url ? t('llmKeyWhere').replace('{url}', p.keys_url) : t('llmKeyCompat'));
  hint.className = `mt-1 text-[11px] leading-snug break-words select-text ${p.key.saved && !p.key.readable ? 'text-apple-red' : 'text-apple-tertiary'}`;
  const links = document.getElementById('llm-key-links');
  links.classList.toggle('hidden', !p.keys_url);
  document.getElementById('llm-key-open').dataset.url = p.keys_url || '';
  document.getElementById('llm-key-copy').dataset.url = p.keys_url || '';
  document.getElementById('llm-key-delete').classList.toggle('hidden', !p.key.saved);
  if (keepModel !== true) setLlmTestResult('', '');
  // The models the key can use, once per provider and session
  state.llmModelsTried = state.llmModelsTried || {};
  if (p.key.saved && p.key.readable && !(state.llmModels && state.llmModels[prov]) && !state.llmModelsTried[prov]) {
    state.llmModelsTried[prov] = true;
    loadLlmModels(false);
  }
}

// The model list: the models the key can use (or the suggestions until it is loaded) and "other…"
function renderLlmModelSelect(prov) {
  const st = state.llm;
  const p = st.providers[prov];
  const input = document.getElementById('set-llm-model');
  const sel = document.getElementById('set-llm-model-select');
  const loaded = state.llmModels && state.llmModels[prov];
  const list = [...(loaded || p.suggested || [])];
  const current = input.value.trim();
  const custom = !!current && !list.includes(current);
  sel.innerHTML = list.map(m => `<option value="${escapeHtml(m)}" ${m === current ? 'selected' : ''}>${escapeHtml(m)}</option>`).join('')
    + (custom ? `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>` : '')
    + `<option value="__custom">${escapeHtml(t('llmModelOther'))}</option>`;
  if (!current && list.length) { sel.value = list[0]; input.value = list[0]; }
  input.classList.toggle('hidden', !state.llmCustomModel);
  const hint = document.getElementById('llm-model-hint');
  hint.textContent = loaded
    ? (custom ? t('llmModelNotInList') : t('llmModelsFromKey').replace('{n}', loaded.length))
    : (p.key.saved ? '' : t('llmModelsAfterKey'));
  hint.className = `mt-1 text-[10.5px] leading-snug ${loaded && custom ? 'text-apple-amber' : 'text-apple-tertiary'}`;
}

function onLlmModelSelect(value) {
  const input = document.getElementById('set-llm-model');
  if (value === '__custom') {
    state.llmCustomModel = true;
    input.classList.remove('hidden');
    input.focus();
    input.select();
    return;
  }
  state.llmCustomModel = false;
  input.value = value;
  input.classList.add('hidden');
  setLlmTestResult('', '');
}

function setLlmTestResult(kind, text) {
  const out = document.getElementById('llm-test-result');
  out.classList.toggle('hidden', !text);
  const cls = { ok: 'bg-apple-green/10 text-apple-green border border-apple-green/30', err: 'bg-apple-red/10 text-apple-red border border-apple-red/30', wait: 'text-apple-secondary animate-pulse' }[kind] || '';
  out.className = `${text ? '' : 'hidden '}text-[11px] leading-snug break-words select-text rounded-lg px-3 py-2 ${cls}`;
  out.textContent = text;
}

function llmSettingsPayload() {
  const prov = document.getElementById('set-llm-provider').value;
  const out = { llm_cloud_provider: prov, [`llm_model_${prov}`]: document.getElementById('set-llm-model').value.trim() };
  if (prov === 'openai_compat') out.llm_base_url_openai_compat = document.getElementById('set-llm-base-url').value.trim();
  return out;
}

async function saveLlmSettingsNow(extra = {}) {
  const res = await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: { ...llmSettingsPayload(), ...extra } }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return false; }
  if (state.system) state.system.settings = data.settings;
  state.llmProviderEdited = false;
  await loadLlmStatus();
  return true;
}

async function setLlmMode(mode) {
  const st = state.llm || {};
  const extra = { llm_provider: mode };
  if (mode === 'cloud' && !st.consent) {
    if (!confirm(t('llmConsentConfirm'))) return;
    extra.llm_cloud_consent = 'true';
  }
  state.llmShowCloud = mode === 'cloud';
  if (await saveLlmSettingsNow(extra)) {
    showToast(mode === 'cloud' ? t('llmModeCloudOn') : t('llmModeLocalOn'));
  }
}

// Saving a key: the server checks which models it can use (and picks one if the chosen one is not
// among them), then the connection is tested right away, so it is clear whether everything works
async function saveLlmKey() {
  const input = document.getElementById('set-llm-key');
  const key = input.value.trim();
  if (!key) { input.focus(); return; }
  const provider = document.getElementById('set-llm-provider').value;
  setLlmTestResult('wait', t('llmKeyChecking'));
  if (!(await saveLlmSettingsNow())) return;
  const res = await fetch('/api/llm/key', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, key }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { setLlmTestResult('err', data.detail || 'Error'); return; }
  input.value = '';
  state.llm = data;
  if ((data.models || []).length) state.llmModels = { ...(state.llmModels || {}), [provider]: data.models };
  const modelInput = document.getElementById('set-llm-model');
  modelInput.value = data.model || modelInput.value;
  state.llmCustomModel = false;
  renderLlmSettings();
  renderSummaryModelSelect();
  if (data.model_changed) showToast(t('llmModelSwitched').replace('{model}', data.model));
  await testLlmConnection();
}

async function deleteLlmKey() {
  const provider = document.getElementById('set-llm-provider').value;
  if (!confirm(t('llmKeyDeleteConfirm'))) return;
  await fetch(`/api/llm/key/${provider}`, { method: 'DELETE' });
  if (state.llmModels) delete state.llmModels[provider];
  await loadLlmStatus();
}

async function loadLlmModels(explicit) {
  const provider = document.getElementById('set-llm-provider').value;
  const btn = document.getElementById('llm-models-btn');
  btn.classList.add('animate-spin');
  try {
    const res = await fetch(`/api/llm/models/${provider}`);
    const data = await res.json().catch(() => ({}));
    const models = data.models || [];
    if (!models.length) {
      if (explicit) setLlmTestResult('err', t('llmModelsNone'));
      return;
    }
    state.llmModels = { ...(state.llmModels || {}), [provider]: models };
    const input = document.getElementById('set-llm-model');
    // The chosen model is not available with this key: suggest the recommended one
    if (!state.llmCustomModel && data.recommended && !models.includes(input.value.trim())) {
      input.value = data.recommended;
      if (explicit) showToast(t('llmModelSwitched').replace('{model}', data.recommended));
    }
    renderLlmModelSelect(provider);
    if (explicit) showToast(t('llmModelsLoaded').replace('{n}', models.length));
  } finally {
    btn.classList.remove('animate-spin');
  }
}

async function testLlmConnection() {
  const provider = document.getElementById('set-llm-provider').value;
  const btn = document.getElementById('llm-test-btn');
  if (!(await saveLlmSettingsNow())) return;
  btn.disabled = true;
  setLlmTestResult('wait', t('llmTesting'));
  try {
    const res = await fetch(`/api/llm/test/${provider}`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Error');
    setLlmTestResult('ok', t('llmTestOk').replace('{model}', data.model).replace('{sec}', data.seconds));
  } catch (e) {
    setLlmTestResult('err', e.message);
  } finally {
    btn.disabled = false;
  }
}

// Web links never open inside the app window: they go to the default browser
async function openExternal(url) {
  if (!url) return;
  try {
    const res = await fetch('/api/open-url', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
    if (!res.ok) throw new Error();
    showToast(t('linkOpened'));
  } catch (e) {
    copyText(url);
  }
}

document.addEventListener('click', (e) => {
  const a = e.target.closest && e.target.closest('a[href]');
  if (!a) return;
  const href = a.getAttribute('href') || '';
  if (/^https?:\/\//i.test(href)) {
    e.preventDefault();
    openExternal(href);
  }
}, true);

async function selectActiveOllamaModel(modelId) {
  if (modelId === '__cloud') {
    await saveLlmSettingsNow({ llm_provider: 'cloud' });
    const c = cloudLlmChoice();
    showToast(c ? `${t('llmModeCloudOn')} · ${c.label}` : t('llmModeCloudOn'));
    renderAnalysisEstimate();
    return;
  }
  setTimeout(renderAnalysisEstimate, 300);
  if (state.llm && state.llm.mode === 'cloud') await saveLlmSettingsNow({ llm_provider: 'ollama' });
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
      update_check: String(document.getElementById('set-update-check').checked),
      quiet_mode: String(document.getElementById('set-quiet-mode').checked),
      vad_filter: String(document.getElementById('set-vad').checked),
      audio_enhance: String(document.getElementById('set-audio-enhance')?.checked !== false),
      clean_fillers: String(document.getElementById('set-clean-fillers')?.checked !== false),
      ollama_model: document.getElementById('set-ollama-model').value.trim(),
      auto_summary: String(document.getElementById('set-auto-summary').checked),
      auto_title: String(document.getElementById('set-auto-title').checked),
      semantic_search: String(document.getElementById('set-semantic').checked),
      semantic_model: document.getElementById('set-semantic-model').value.trim() || 'bge-m3',
      idle_unload_minutes: document.getElementById('set-idle-unload').value,
      ...obsidianSettingsPayload(),
      ...llmSettingsPayload(),
      record_source_mode: document.getElementById('set-record-source')?.value || 'both',
      record_mic_device: document.getElementById('set-record-mic')?.value || '',
      live_whisper_model: document.getElementById('set-live-model')?.value || 'auto',
      record_hotkey: document.getElementById('set-record-hotkey')?.value.trim() || '',
      ...callSettingsPayload(),
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

async function pullOllamaModel(modelArg, makeActive = true) {
  const model = (modelArg || document.getElementById('set-ollama-model').value || 'qwen2.5:7b').trim();
  let allowInstall = false;
  if (state.system && state.system.ollama && !state.system.ollama.installed) {
    allowInstall = confirm(state.uiLang === 'en'
      ? 'Ollama is not installed. Install it now via winget (package Ollama.Ollama, about 1 GB)?\n\nYou can also install it manually from https://ollama.com/download'
      : 'Ollama не установлена. Установить её сейчас через winget (пакет Ollama.Ollama, около 1 ГБ)?\n\nМожно установить и вручную: https://ollama.com/download');
    if (!allowInstall) return;
  }
  if (makeActive) document.getElementById('set-ollama-model').value = model;  // not for the embedding model
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
  document.getElementById('dict-clear-btn').classList.toggle('hidden', state.dictionary.length === 0);
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

async function clearDictionary() {
  if (!confirm(t('dictClearConfirm').replace('{n}', state.dictionary.length))) return;
  await fetch('/api/dictionary', { method: 'DELETE' });
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
