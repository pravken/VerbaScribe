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
    const data = await readJson(res);
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
    document.getElementById('set-delete-media-after').checked = s.delete_media_after === 'true';
    document.getElementById('set-media-retention').value = s.media_retention_days || '0';
    document.getElementById('set-update-auto').checked = s.update_auto !== 'false';
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
    document.getElementById('set-summary-grounding').checked = s.summary_grounding !== 'false';
    document.getElementById('set-auto-commitments').checked = s.auto_commitments !== 'false';
    document.getElementById('set-quiet-mode').checked = s.quiet_mode === 'true';
    loadWatchFolder();
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
    document.getElementById('set-dictation-hotkey').value = s.dictation_hotkey ?? 'Ctrl+Alt+D';
    document.getElementById('set-dictation-language').value = s.dictation_language || 'auto';
    document.getElementById('set-calendar-ics').value = s.calendar_ics || '';
    loadCalendarSource();
    document.getElementById('set-onnx-gpu').checked = s.onnx_gpu !== 'false';
    loadBackupInfo();
    loadOnnxGpu();
    document.getElementById('calendar-status').textContent = '';
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
        openFirstRunWizard(data);
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
    if (res.ok) renderVram(await readJson(res));
  } catch (e) {
    // the server may be restarting; try again later
  } finally {
    // Without a video card only the models in memory change: asked less often
    const noGpu = state.system && state.system.hardware && !state.system.hardware.cuda_available;
    scheduleVramRefresh(VRAM_POLL_MS * (document.hidden ? 4 : 1) * (noGpu ? 3 : 1));
  }
}

function vramChipLabel(c) {
  if (c.kind === 'dub') return `${t('vramChipDub')}${c.count > 1 ? ' ×' + c.count : ''}`;
  if (c.kind === 'speakers') return t('vramChipSpeakers');
  if (c.kind === 'live') return `${t('vramChipLive')} · ${c.label || c.name}`;
  if (c.kind === 'onnx') return `${c.label || c.name}${c.device === 'cuda' ? ' · GPU' : ''}${c.live ? ' · ' + t('vramChipLive') : ''}`;
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
  'gigaam-v3': 'GigaAM v3',
  'parakeet-v3': 'Parakeet TDT v3',
  'large-v3-turbo': 'Whisper Turbo',
  'large-v3': 'Whisper Pro',
  'qwen3:8b': 'Qwen3 8B',
  'qwen3:14b': 'Qwen3 14B',
  'qwen3.5:4b': 'Qwen3.5 4B',
  'qwen3:4b': 'Qwen3 4B',
  'qwen2.5:7b': 'Qwen 7B Standard',
  'qwen2.5:14b': 'Qwen 14B Pro',
  'mistral:7b': 'Mistral 7B',
  'qwen2.5:1.5b': 'Qwen Lite',
      };

// GigaAM / Parakeet (app/asr_onnx.py): downloaded by the server, progress shown as a pulsing label
async function startOnnxModelDownload(id) {
  const res = await fetch(`/api/onnx-models/${encodeURIComponent(id)}/download`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  showToast(t('onnxDownloading'));
  const poll = async () => {
    const models = await fetch('/api/models/status').then(r => r.json()).catch(() => null);
    if (models) updateWhisperModelsUI(models);
    const item = models && (models.whisper_catalog || []).find(x => x.id === id);
    if (item && item.downloading) setTimeout(poll, 2000);
    else if (item && item.error) showToast(item.error);
    else if (item && item.installed) showToast(t('onnxReady'));
  };
  setTimeout(poll, 1500);
}

async function deleteOnnxModel(id) {
  const item = (state.whisperCatalog || []).find(i => i.id === id) || {};
  if (!(await confirmModelDelete(formatModelName(id), item.path))) return;
  await fetch(`/api/onnx-models/${encodeURIComponent(id)}`, { method: 'DELETE' });
  const models = await fetch('/api/models/status').then(r => r.json()).catch(() => null);
  if (models) updateWhisperModelsUI(models);
}

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
  state.whisperInstalled = whisperList.filter(i => i.installed && i.engine !== 'onnx').map(i => i.id);
  state.onnxInstalled = whisperList.filter(i => i.installed && i.engine === 'onnx').map(i => i.id);
  state.whisperCatalog = whisperList;
  fillRecognitionSelects(whisperList, activeModel);
  renderLiveModelSelect();

  if (catalogBox && whisperList.length > 0) {
    catalogBox.innerHTML = whisperList.map(item => {
      const isSelected = item.id === activeModel;
      const desc = state.uiLang === 'en' ? item.desc_en : item.desc_ru;
      const isDownloadingThis = (st.active && st.model_id === item.id) || (item.engine === 'onnx' && item.downloading);
      const sizeLabel = item.size || (item.size_mb >= 1000 ? (item.size_mb / 1024).toFixed(1) + ' GB' : (item.size_mb || '') + ' MB');
      const badgeLabel = item.badge || sizeLabel;

      let actionHtml = '';
      if (isDownloadingThis) {
        actionHtml = `<span class="ui-btn bg-apple-blue/20 text-apple-blue font-mono animate-pulse">${(item.engine === 'onnx' ? item.progress : st.progress) || 1}%</span>`;
      } else if (item.installed && isSelected) {
        actionHtml = `
          <div class="flex items-center gap-1.5">
            <span class="ui-btn bg-apple-green/20 text-apple-green">${SVG_ICONS.check}<span>${state.uiLang === 'en' ? 'Active' : 'Активна'}</span></span>
            <button data-id="${escapeHtml(item.id)}" onclick="${item.engine === 'onnx' ? 'deleteOnnxModel' : 'deleteWhisperModel'}(this.dataset.id)" title="${escapeHtml(t('tipDeleteModel'))}"
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
            <button data-id="${escapeHtml(item.id)}" onclick="${item.engine === 'onnx' ? 'deleteOnnxModel' : 'deleteWhisperModel'}(this.dataset.id)" title="${escapeHtml(t('tipDeleteModel'))}"
              class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button>
          </div>
        `;
      } else {
        actionHtml = `
          <button data-id="${escapeHtml(item.id)}" onclick="${item.engine === 'onnx' ? 'startOnnxModelDownload' : 'startWhisperModelDownload'}(this.dataset.id)" title="${escapeHtml(item.error || t('tipDownloadModel'))}"
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
            ${item.installed ? modelLocationHtml(item.path, item.disk_mb) : ''}
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

// --- First run: a speech model (and an analysis model) chosen for this computer ---
function firstRunPicks(data, lang) {
  const hw = data.hardware || {};
  const gpu = hw.cuda_available && Number(hw.vram_total_gb || 0) >= 4;
  const vram = Number(hw.vram_total_gb || 0);
  const speech = [];
  if (lang === 'ru') speech.push({ id: 'gigaam-v3', why: t('frWhyGigaam') });
  // With a GPU Whisper is the more accurate one outside Russian (bench-report/gpu-report.md), Parakeet the fast one
  if (gpu) speech.push({ id: 'large-v3-turbo', why: t('frWhyTurbo') });
  speech.push({ id: 'parakeet-v3', why: t('frWhyParakeet') });
  if (!gpu && lang !== 'ru') speech.push({ id: 'large-v3-turbo', why: t('frWhyTurboCpu') });
  const llm = vram >= 10 ? 'qwen3:8b' : 'qwen3.5:4b';  // bench-report/report.md
  return { speech: speech.slice(0, 3), llm, gpu };
}

function openFirstRunWizard(data) {
  document.getElementById('first-run-wizard')?.remove();
  const wrap = document.createElement('div');
  wrap.id = 'first-run-wizard';
  wrap.className = 'fixed inset-0 modal-backdrop z-[60] flex items-center justify-center p-4';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  const st = { step: 0, lang: state.uiLang === 'en' ? 'en' : 'ru', speech: null, llm: true };
  const hw = data.hardware || {};
  const ollama = data.ollama || {};
  const card = (key, title, sub, on) => `<button data-pick="${escapeHtml(key)}" class="w-full text-left p-3 rounded-xl border transition ${on ? 'bg-apple-blue/15 border-apple-blue/60' : 'bg-apple-sidebar border-white/10 hover:border-white/25'}">
      <div class="text-xs font-semibold text-white">${title}</div><div class="text-[11px] text-apple-secondary mt-0.5 leading-snug">${sub}</div></button>`;
  const draw = () => {
    const picks = firstRunPicks(data, st.lang);
    if (!st.speech || !picks.speech.some(p => p.id === st.speech)) st.speech = picks.speech[0].id;
    let body = '';
    if (st.step === 0) {
      body = `<p class="text-xs text-apple-secondary leading-relaxed">${escapeHtml(t('frIntro'))}</p>
        <div class="text-[11px] text-apple-tertiary">${escapeHtml(t('frHardware'))}: <span class="text-apple-label">${escapeHtml(hw.device_name || '—')}${hw.vram_total_gb ? ` · ${hw.vram_total_gb} ${t('unitGB')}` : ''}</span></div>
        <div class="text-xs text-white font-medium pt-1">${escapeHtml(t('frLangQ'))}</div>
        <div class="grid grid-cols-3 gap-2">${[['ru', t('frLangRu')], ['en', t('frLangEn')], ['mix', t('frLangMix')]].map(([k, l]) =>
          `<button data-lang="${k}" class="ui-btn h-9 ${st.lang === k ? 'ui-btn-primary' : 'ui-btn-subtle'}">${escapeHtml(l)}</button>`).join('')}</div>`;
    } else if (st.step === 1) {
      body = `<div class="text-xs text-white font-medium">${escapeHtml(t('frSpeechQ'))}</div>
        <div class="space-y-2">${picks.speech.map((p, i) => card(p.id, escapeHtml(formatModelName(p.id)) + (i === 0 ? ` <span class="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-apple-green/20 text-apple-green">${escapeHtml(t('frRecommended'))}</span>` : ''), escapeHtml(p.why), st.speech === p.id)).join('')}</div>`;
    } else if (st.step === 2) {
      body = `<div class="text-xs text-white font-medium">${escapeHtml(t('frLlmQ'))}</div>
        <p class="text-[11px] text-apple-secondary leading-snug">${escapeHtml(t('frLlmDesc'))}</p>
        <div class="space-y-2">
          ${card('llm-yes', escapeHtml(formatModelName(picks.llm)) + ` <span class="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-apple-green/20 text-apple-green">${escapeHtml(t('frRecommended'))}</span>`, escapeHtml(ollama.installed ? t('frLlmLocal') : t('frLlmNoOllama')), st.llm)}
          ${card('llm-no', escapeHtml(t('frLlmSkip')), escapeHtml(t('frLlmSkipDesc')), !st.llm)}
        </div>`;
    }
    wrap.innerHTML = `<div class="w-full max-w-lg bg-apple-card border apple-border rounded-2xl shadow-2xl modal-card">
      <div class="px-5 pt-5 pb-3 border-b apple-border flex items-center justify-between">
        <div class="text-sm font-semibold text-white">${escapeHtml(t('frTitle'))}</div>
        <div class="text-[11px] text-apple-tertiary font-mono">${st.step + 1} / 3</div>
      </div>
      <div class="px-5 py-4 space-y-3">${body}</div>
      <div class="px-5 py-3 border-t apple-border flex items-center gap-2">
        <button data-a="later" class="ui-btn ui-btn-subtle mr-auto">${escapeHtml(t('frLater'))}</button>
        ${st.step > 0 ? `<button data-a="back" class="ui-btn ui-btn-subtle">${escapeHtml(t('frBack'))}</button>` : ''}
        <button data-a="next" class="ui-btn ui-btn-primary">${escapeHtml(st.step < 2 ? t('frNext') : t('frStart'))}</button>
      </div></div>`;
  };
  draw();
  wrap.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.lang) { st.lang = b.dataset.lang; draw(); return; }
    if (b.dataset.pick) {
      if (b.dataset.pick === 'llm-yes') st.llm = true;
      else if (b.dataset.pick === 'llm-no') st.llm = false;
      else st.speech = b.dataset.pick;
      draw();
      return;
    }
    if (b.dataset.a === 'later') { wrap.remove(); openModal('settings', 'recognition'); return; }
    if (b.dataset.a === 'back') { st.step--; draw(); return; }
    if (b.dataset.a === 'next' && st.step < 2) { st.step++; draw(); return; }
    if (b.dataset.a === 'next') {
      wrap.remove();
      const picks = firstRunPicks(data, st.lang);
      const onnx = (state.whisperCatalog || []).some(i => i.id === st.speech && i.engine === 'onnx');
      await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: { whisper_model: st.speech, language: st.lang === 'mix' ? 'auto' : st.lang } }) });
      if (onnx) startOnnxModelDownload(st.speech); else startWhisperModelDownload(st.speech);
      if (st.llm) pullOllamaModel(picks.llm, true);
      showToast(t('frDownloading'));
    }
  });
  document.body.appendChild(wrap);
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

// Where a downloaded model is on the disk, with its size and a button to show it in Explorer
function modelLocationHtml(path, mb, label) {
  if (!path) return '';
  const size = mb ? ` · ${mb >= 1024 ? (mb / 1024).toFixed(1) + ' ' + t('unitGB') : mb + ' ' + t('unitMB')}` : '';
  return `<div class="mt-1.5 flex items-center gap-1.5 min-w-0 text-[10.5px] text-apple-tertiary">
      <span class="shrink-0">${escapeHtml(label || t('modelLocation'))}${size}:</span>
      <span class="font-mono truncate" title="${escapeHtml(path)}">${escapeHtml(path)}</span>
      <button data-path="${escapeHtml(path)}" onclick="revealPath(this.dataset.path)" class="shrink-0 text-apple-blue hover:underline">${escapeHtml(t('modelReveal'))}</button>
    </div>`;
}

async function confirmModelDelete(name, path) {
  return confirmDialog(t('modelDeleteTitle'), t('modelDeleteText').replace('{name}', name) + (path ? `\n${path}` : ''), t('modelDeleteBtn'));
}

async function deleteWhisperModel(modelId) {
  const pretty = formatModelName(modelId);
  const item = (state.whisperCatalog || []).find(i => i.id === modelId) || {};
  if (!(await confirmModelDelete(pretty, item.path))) return;
  const res = await fetch(`/api/models/${encodeURIComponent(modelId)}`, { method: 'DELETE' });
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
  await loadSystemInfo();
  showToast(state.uiLang === 'en' ? `Deleted ${pretty} from disk` : `Модель ${pretty} удалена с диска`);
}

async function deleteOllamaModel(name) {
  if (!(await confirmModelDelete(formatModelName(name), ''))) return;
  const res = await fetch(`/api/ollama/models/${encodeURIComponent(name)}`, { method: 'DELETE' });
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
  updateOllamaUI(await readJson(res));
  showToast(t('modelDeleted').replace('{name}', formatModelName(name)));
}

function startWhisperPollingLoop(modelId) {
  clearInterval(window._whisperPollTimer);
  window._whisperPollTimer = setInterval(async () => {
    try {
      const res = await fetch('/api/models/status');
      const data = await readJson(res);
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
      } else if (item.installed) {
        const del = `<button data-id="${escapeHtml(item.id)}" onclick="deleteOllamaModel(this.dataset.id)" title="${escapeHtml(t('tipDeleteModel'))}"
              class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button>`;
        actionHtml = isSelected
          ? `<div class="flex items-center gap-1.5"><span class="ui-btn bg-apple-green/20 text-apple-green">${SVG_ICONS.check}<span>${state.uiLang === 'en' ? 'Active' : 'Активна'}</span></span>${del}</div>`
          : `<div class="flex items-center gap-1.5">
          <button data-id="${escapeHtml(item.id)}" onclick="selectActiveOllamaModel(this.dataset.id)" title="${escapeHtml(t('tipSelectModel'))}"
            class="ui-btn ui-btn-subtle text-white hover:bg-apple-blue">
            ${state.uiLang === 'en' ? 'Select' : 'Выбрать'}
          </button>${del}</div>`;
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
            ${item.installed ? modelLocationHtml(ollama.models_dir, 0, t('modelLocationOllama')) : ''}
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

// --- Auto-import folder (app/watch_folder.py) ---
function renderWatchFolder(st) {
  document.getElementById('watch-folder-path').textContent = st.folder || t('watchOff');
  const box = document.getElementById('watch-folder-status');
  box.textContent = st.error ? st.error : st.folder ? t('watchOn').replace('{n}', st.imported || 0) + (st.last_import ? ` · ${st.last_import}` : '') : '';
  box.className = 'text-[11px] ' + (st.error ? 'text-apple-amber' : 'text-apple-tertiary');
}

async function loadWatchFolder() {
  const st = await fetch('/api/watch-folder').then(r => r.json()).catch(() => null);
  if (st) renderWatchFolder(st);
}

async function pickWatchFolder() {
  const st = await fetch('/api/watch-folder/pick', { method: 'POST' }).then(r => r.json()).catch(() => null);
  if (st && st.ok) { renderWatchFolder(st); showToast(t('watchSet')); }
}

async function watchFolderOff() {
  const st = await fetch('/api/watch-folder/off', { method: 'POST' }).then(r => r.json()).catch(() => null);
  if (st) renderWatchFolder(st);
}

// --- Backup of the library (app/backup.py) ---
async function loadBackupInfo() {
  const st = await fetch('/api/backup').then(r => r.json()).catch(() => null);
  const el = document.getElementById('backup-media-size');
  if (st && el) el.textContent = st.media_files ? `(${st.media_files} · ${st.media_mb} MB)` : '';
}

async function makeBackup() {
  const box = document.getElementById('backup-status');
  const btn = document.getElementById('backup-make-btn');
  btn.disabled = true;
  box.className = 'text-[11px] text-apple-tertiary';
  box.textContent = t('backupMaking');
  try {
    const res = await fetch('/api/backup', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ with_media: document.getElementById('backup-with-media').checked }) });
    const data = await readJson(res);
    if (!res.ok) throw new Error(data.detail || 'Error');
    box.textContent = data.cancelled ? '' : t('backupMade').replace('{path}', data.path).replace('{mb}', data.size_mb);
  } catch (e) {
    box.className = 'text-[11px] text-apple-amber';
    box.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function restoreBackup() {
  if (!(await confirmDialog(t('backupRestoreTitle'), t('backupRestoreText'), t('backupRestore').replace('…', '')))) return;
  const box = document.getElementById('backup-status');
  box.className = 'text-[11px] text-apple-tertiary';
  box.textContent = t('backupReading');
  const res = await fetch('/api/backup/restore', { method: 'POST' });
  const data = await readJson(res);
  if (!res.ok) { box.className = 'text-[11px] text-apple-amber'; box.textContent = data.detail || 'Error'; return; }
  if (data.cancelled) { box.textContent = ''; return; }
  box.className = 'text-[11px] text-apple-green';
  box.textContent = t('backupRestored').replace('{n}', data.recordings).replace('{m}', data.media_restored);
}

// --- GigaAM / Parakeet on the NVIDIA GPU (app/onnx_gpu.py) ---
let _onnxGpuTimer = null;

function renderOnnxGpu(st) {
  const box = document.getElementById('onnx-gpu-box');
  box.classList.toggle('hidden', !st.nvidia);
  if (!st.nvidia) return;
  const label = document.getElementById('onnx-gpu-status');
  label.className = 'text-[11px] ' + (st.error ? 'text-apple-amber' : st.active ? 'text-apple-green' : 'text-apple-secondary');
  label.textContent = st.installing ? t('onnxGpuInstalling')
    : st.error ? st.error
    : st.removal_pending ? t('onnxGpuRemovePending')
    : st.active ? t('onnxGpuActive')
    : st.restart_needed ? t('onnxGpuRestart')
    : st.installed ? t('onnxGpuNoCuda')
    : t('onnxGpuMissing');
  document.getElementById('onnx-gpu-install').classList.toggle('hidden', st.installed || st.installing);
  document.getElementById('onnx-gpu-remove').classList.toggle('hidden', !st.installed || st.installing);
  clearTimeout(_onnxGpuTimer);
  if (st.installing) _onnxGpuTimer = setTimeout(loadOnnxGpu, 2000);
}

async function loadOnnxGpu() {
  const st = await fetch('/api/onnx-gpu').then(r => r.json()).catch(() => null);
  if (st) renderOnnxGpu(st);
}

async function installOnnxGpu() {
  const res = await fetch('/api/onnx-gpu/install', { method: 'POST' });
  const data = await readJson(res);
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  document.getElementById('set-onnx-gpu').checked = true;
  renderOnnxGpu(data);
}

async function removeOnnxGpu() {
  if (!await askConfirm(t('onnxGpuRemoveConfirm'))) return;
  const data = await fetch('/api/onnx-gpu/remove', { method: 'POST' }).then(r => r.json()).catch(() => null);
  document.getElementById('set-onnx-gpu').checked = false;
  if (data) renderOnnxGpu(data);
}

// --- The user's calendar (app/calendar_ics.py; "outlook": app/calendar_outlook.py) ---
async function loadCalendarSource() {
  const st = await fetch('/api/calendar/upcoming').then(r => r.json()).catch(() => null);
  document.getElementById('calendar-outlook-btn')?.classList.toggle('hidden', !(st && st.outlook));
}

function useOutlookCalendar() {
  document.getElementById('set-calendar-ics').value = 'outlook';
  checkCalendar();
}

async function checkCalendar() {
  const box = document.getElementById('calendar-status');
  const source = document.getElementById('set-calendar-ics').value.trim();
  box.className = 'text-[11px] text-apple-tertiary';
  box.textContent = t('calendarChecking');
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: { calendar_ics: source } }) }).catch(() => null);
  const st = await fetch('/api/calendar/upcoming?refresh=true').then(r => r.json()).catch(() => null);
  if (!st || !st.configured) { box.textContent = source ? 'Error' : t('watchOff'); return; }
  if (st.error) { box.className = 'text-[11px] text-apple-amber'; box.textContent = st.error; return; }
  box.textContent = t('calendarOk').replace('{n}', st.count).replace('{next}', st.events.length);
}

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
    // Program names as people know them: "chrome.exe" -> "chrome", no store package suffixes
    const names = [...new Set((st.mic_now || []).map(n => n.replace(/_[a-z0-9]{13}$/, '').replace(/\.exe$/i, '')))];
    box.textContent = `${t('callMicNow')}: ${names.length ? names.join(', ') : t('callMicNobody')}`
      + (st.active ? ` · ${t('callActive').replace('{app}', st.active)}` : '')
      + (st.running ? '' : ` · ${t('callWatcherOff')}`);
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
// Upload and re-transcribe dialogs: the downloaded models (Whisper, GigaAM, Parakeet)
function fillRecognitionSelects(list, active) {
  const items = list.filter(i => i.installed);
  ['upload-model', 'reconfig-model'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    if (!items.length) {  // nothing downloaded yet: no model to offer (a job would fail)
      sel.innerHTML = `<option value="">${escapeHtml(t('noModelInstalled'))}</option>`;
      return;
    }
    const value = sel.value;
    sel.innerHTML = items.map(i => `<option value="${escapeHtml(i.id)}">${escapeHtml(formatModelName(i.id))}</option>`).join('');
    sel.value = items.some(i => i.id === value) ? value : (items.some(i => i.id === active) ? active : items[0].id);
  });
}

function renderLiveModelSelect() {
  const sel = document.getElementById('set-live-model');
  if (!sel) return;
  const value = sel.dataset.touched ? sel.value : (state.liveModelSetting || 'auto');
  const ids = [...(state.onnxInstalled || []), ...(state.whisperInstalled || [])];
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
      ? `<div class="flex items-center gap-1.5"><button onclick="buildSemanticIndex()" class="ui-btn ui-btn-subtle text-white hover:bg-apple-blue">${escapeHtml(t('semanticBuild'))}</button>
          <button data-id="${escapeHtml((ollama.models || []).find(n => n === model || n.startsWith(model + ':')) || model)}" onclick="deleteOllamaModel(this.dataset.id)" title="${escapeHtml(t('tipDeleteModel'))}"
            class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button></div>`
      : `<button onclick="pullSemanticModel()" class="ui-btn ui-btn-primary">${SVG_ICONS.download}<span>${escapeHtml(t('semanticDownload'))} (1.2 GB)</span></button>`;
  box.innerHTML = `<div class="p-3 rounded-xl border transition flex items-center justify-between gap-3 ${installed ? 'bg-apple-blue/10 border-apple-blue/50' : 'bg-apple-sidebar border-white/10'}">
    <div class="min-w-0 flex-1">
      <div class="flex items-center flex-wrap gap-2">
        <span class="text-xs font-semibold text-white">BGE-M3</span>
        <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">1.2 GB · MIT</span>
        <span class="text-[10px] text-apple-tertiary">${installed ? '✓ ' + escapeHtml(t('semanticInstalled')) : '1+ GB VRAM / CPU'}</span>
      </div>
      <p class="text-[11.5px] text-apple-secondary mt-1 leading-snug">${escapeHtml(t('semanticModelDesc'))}</p>
      ${installed ? modelLocationHtml(ollama.models_dir, 0, t('modelLocationOllama')) : ''}
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
    if (res.ok) state.llm = await readJson(res);
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
    if (!await askConfirm(t('llmConsentConfirm'))) return;
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
  if (!await askConfirm(t('llmKeyDeleteConfirm'))) return;
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
const SUPPORT_URL = 'https://ko-fi.com/pravken';

// What left the computer: every request to a cloud model, with its size and purpose
async function openCloudLog() {
  const rows = await fetch('/api/privacy/cloud-log').then(r => r.json()).catch(() => []);
  document.getElementById('cloud-log')?.remove();
  const wrap = document.createElement('div');
  wrap.id = 'cloud-log';
  wrap.className = 'fixed inset-0 modal-backdrop z-[70] flex items-center justify-center p-4';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.innerHTML = `<div class="w-full max-w-2xl max-h-[85vh] flex flex-col bg-apple-card border apple-border rounded-2xl shadow-2xl modal-card">
      <div class="px-5 pt-5 pb-3 border-b apple-border flex items-center justify-between gap-3">
        <div><div class="text-sm font-semibold text-white">${escapeHtml(t('cloudLogBtn'))}</div>
          <p class="text-[11px] text-apple-secondary mt-1">${escapeHtml(t('cloudLogDesc'))}</p></div>
        <div class="flex gap-2 shrink-0">
          ${rows.length ? `<button data-a="clear" class="ui-btn ui-btn-subtle">${escapeHtml(t('cloudLogClear'))}</button>` : ''}
          <button data-a="close" class="ui-btn ui-btn-subtle">${escapeHtml(t('close'))}</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto px-5 py-2">${rows.length ? rows.map(r => `<div class="py-2 border-b border-white/5 text-[11.5px]">
          <div class="flex justify-between gap-3"><span class="text-white">${escapeHtml(r.provider)} · ${escapeHtml(r.model)}</span>
            <span class="font-mono text-apple-tertiary shrink-0">${escapeHtml(r.at.replace('T', ' '))} · ${r.tokens} tok</span></div>
          <div class="text-apple-secondary truncate" title="${escapeHtml(r.purpose)}">${escapeHtml(r.purpose)}</div></div>`).join('')
        : `<div class="py-10 text-center text-xs text-apple-tertiary">${escapeHtml(t('cloudLogEmpty'))}</div>`}</div>
    </div>`;
  wrap.addEventListener('click', async e => {
    if (e.target === wrap || e.target.closest('[data-a="close"]')) wrap.remove();
    else if (e.target.closest('[data-a="clear"]')) { await fetch('/api/privacy/cloud-log', { method: 'DELETE' }); wrap.remove(); openCloudLog(); }
  });
  document.body.appendChild(wrap);
}

// mailto: links go to the mail app (the window itself never navigates away)
function openExternalMail(url) {
  fetch('/api/open-url', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) })
    .then(r => { if (!r.ok) location.href = url; }).catch(() => { location.href = url; });
}

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
      delete_media_after: String(document.getElementById('set-delete-media-after').checked),
      media_retention_days: document.getElementById('set-media-retention').value || '0',
      update_auto: String(document.getElementById('set-update-auto').checked),
      quiet_mode: String(document.getElementById('set-quiet-mode').checked),
      vad_filter: String(document.getElementById('set-vad').checked),
      audio_enhance: String(document.getElementById('set-audio-enhance')?.checked !== false),
      clean_fillers: String(document.getElementById('set-clean-fillers')?.checked !== false),
      ollama_model: document.getElementById('set-ollama-model').value.trim(),
      auto_summary: String(document.getElementById('set-auto-summary').checked),
      auto_title: String(document.getElementById('set-auto-title').checked),
      summary_grounding: String(document.getElementById('set-summary-grounding').checked),
      auto_commitments: String(document.getElementById('set-auto-commitments').checked),
      semantic_search: String(document.getElementById('set-semantic').checked),
      semantic_model: document.getElementById('set-semantic-model').value.trim() || 'bge-m3',
      idle_unload_minutes: document.getElementById('set-idle-unload').value,
      ...obsidianSettingsPayload(),
      ...llmSettingsPayload(),
      record_source_mode: document.getElementById('set-record-source')?.value || 'both',
      record_mic_device: document.getElementById('set-record-mic')?.value || '',
      live_whisper_model: document.getElementById('set-live-model')?.value || 'auto',
      record_hotkey: document.getElementById('set-record-hotkey')?.value.trim() || '',
      dictation_hotkey: document.getElementById('set-dictation-hotkey')?.value.trim() || '',
      dictation_language: document.getElementById('set-dictation-language')?.value || 'auto',
      calendar_ics: document.getElementById('set-calendar-ics')?.value.trim() || '',
      onnx_gpu: document.getElementById('set-onnx-gpu')?.checked ? 'true' : 'false',
      ...callSettingsPayload(),
      ...dubSettingsPayload(),
    }
  };
  // "Saved" only once the server has the settings: on an error the window stays open with them
  let res;
  try {
    res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  } catch (e) {
    showToast(t('settingsSaveFailed').replace('{why}', () => String(e.message || e)));
    return;
  }
  if (!res.ok) {
    const why = errorText(await readJson(res), res);
    showToast(t('settingsSaveFailed').replace('{why}', () => why));
    return;
  }
  closeModal('settings');
  showToast(t('settingsSaved'));
  loadSystemInfo().catch(console.error);
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
      const data = await readJson(res);
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
    allowInstall = await askConfirm(state.uiLang === 'en'
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
  const terms = await readJson(res);
  if (!res.ok || !Array.isArray(terms)) return;
  state.dictionary = terms;
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
  if (!await askConfirm(t('dictClearConfirm').replace('{n}', state.dictionary.length))) return;
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

// --- Model comparison on the user's recordings (app/bench.py) ---
const bench = { kind: 'asr', data: null, timer: null, picked: {} };

async function loadBench() {
  const data = await fetch('/api/bench?choices=true').then(r => r.json()).catch(() => null);
  if (!data) return;
  bench.data = data;
  if (data.running) bench.kind = data.kind;
  const sel = document.getElementById('bench-meeting');
  const keep = sel.value;
  // Checked transcripts first: only they are a real reference
  const rows = (state.meetings || []).filter(m => m.status === 'completed')
    .sort((a, b) => (b.transcript_verified ? 1 : 0) - (a.transcript_verified ? 1 : 0));
  sel.innerHTML = rows.length
    ? rows.map(m => `<option value="${m.id}">${m.transcript_verified ? '✓ ' : ''}${escapeHtml(m.title || '')}${m.duration ? ` · ${formatDurationShort(m.duration)}` : ''}${m.transcript_verified ? '' : ` · ${escapeHtml(t('benchNotVerified'))}`}</option>`).join('')
    : `<option value="">${escapeHtml(t('benchNoRecordings'))}</option>`;
  if (data.running && data.meeting_id) sel.value = String(data.meeting_id);
  else if (keep && rows.some(m => String(m.id) === keep)) sel.value = keep;
  renderBench();
  if (data.running) pollBench();
}

function benchSetKind(kind) {
  if (bench.data && bench.data.running) return;
  bench.kind = kind;
  renderBench();
}

function benchModels() {
  const d = bench.data || {};
  return (bench.kind === 'asr' ? d.asr_models : d.llm_models) || [];
}

function renderBench() {
  const d = bench.data || {};
  ['asr', 'llm'].forEach(k => {
    document.getElementById(`bench-kind-${k}`).className = `apple-segment-btn text-[11px] ${bench.kind === k ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  });
  document.getElementById('bench-clip').classList.toggle('hidden', bench.kind !== 'asr');
  const models = benchModels();
  const picked = bench.picked[bench.kind] || (bench.picked[bench.kind] = new Set(models.map(m => m.id)));
  const box = document.getElementById('bench-models');
  box.innerHTML = models.length
    ? models.map(m => `<label class="flex items-center gap-1.5 cursor-pointer text-xs text-apple-label">
        <input type="checkbox" ${picked.has(m.id) ? 'checked' : ''} ${d.running ? 'disabled' : ''} onchange="benchPick(${jsArg(m.id)}, this.checked)"
          class="rounded bg-apple-sidebar border-white/20 text-apple-blue" />
        <span>${escapeHtml(m.name || m.id)}</span>${m.engine === 'onnx' ? '<span class="text-[10px] text-apple-tertiary">CPU</span>' : ''}</label>`).join('')
    : `<span class="text-[11px] text-apple-tertiary">${escapeHtml(t(bench.kind === 'asr' ? 'benchNoAsr' : 'benchNoLlm'))}</span>`;
  document.getElementById('bench-start').classList.toggle('hidden', !!d.running);
  document.getElementById('bench-cancel').classList.toggle('hidden', !d.running);
  const st = document.getElementById('bench-status');
  st.className = 'text-[11px] ' + (d.error && !d.running ? 'text-apple-amber' : 'text-apple-tertiary');
  st.textContent = d.running
    ? `${d.done + 1}/${d.total} · ${benchModelName(d.model)} · ${d.stage || ''}`
    : d.error || d.busy || '';
  const live = d.running || (d.results && d.results.length && d.kind === bench.kind)
    ? { kind: d.kind, results: d.results || [], title: d.title } : null;
  document.getElementById('bench-results').innerHTML = live && live.results.length ? benchTable(live) : '';
  const hist = (d.history || []).filter(h => h.kind === bench.kind);
  document.getElementById('bench-history').innerHTML = hist.length
    ? `<div class="flex items-center justify-between pt-2"><span class="text-[11px] uppercase tracking-wider text-apple-tertiary">${escapeHtml(t('benchHistory'))}</span>
         <button onclick="clearBenchHistory()" class="text-[11px] text-apple-tertiary hover:text-white">${escapeHtml(t('benchClear'))}</button></div>`
      + hist.map((h, i) => `<details class="rounded-lg border apple-border px-3 py-1.5" ${i === 0 && !live ? 'open' : ''}>
          <summary class="cursor-pointer text-[12px] text-apple-label">${escapeHtml(h.at)} · ${h.verified ? '✓ ' : ''}${escapeHtml(h.title)}${h.kind === 'asr' && !h.verified ? ` · <span class="text-apple-amber">${escapeHtml(t('benchNotVerified'))}</span>` : ''}${h.clip_sec ? ` · ${formatDurationShort(h.clip_sec)}` : ''}${h.ref_words ? ` · ${h.ref_words} ${escapeHtml(t('benchWords'))}` : ''}</summary>
          <div class="pt-2">${benchTable(h)}</div></details>`).join('')
    : '';
}

function benchPick(id, on) {
  const set = bench.picked[bench.kind];
  if (on) set.add(id); else set.delete(id);
}

function benchModelName(id) {
  const all = [...((bench.data || {}).asr_models || []), ...((bench.data || {}).llm_models || [])];
  const m = all.find(x => x.id === id);
  return m ? (m.name || id) : (id || '');
}

function benchTable(run) {
  const ok = run.results.filter(r => !r.error);
  const best = key => ok.length > 1 ? Math.min(...ok.map(r => r[key])) : null;
  const cell = (v, isBest) => `<td class="px-2 py-1 text-right font-mono ${isBest ? 'text-apple-green' : ''}">${v}</td>`;
  // The work, and under it the loading of the model (timed apart, each model from an empty memory)
  const timeCell = (r, isBest) => `<td class="px-2 py-1 text-right font-mono ${isBest ? 'text-apple-green' : ''}">${r.sec} s${r.load_sec != null
    ? `<div class="text-[10px] text-apple-tertiary font-sans">${escapeHtml(t('benchLoad'))} ${r.load_sec} s</div>` : ''}</td>`;
  const useBtn = r => r.error ? '' : `<button onclick="benchUse(${jsArg(run.kind)}, ${jsArg(r.model)})" class="text-[11px] text-apple-blue hover:underline">${escapeHtml(t('benchUse'))}</button>`;
  const mem = r => [r.vram_mb ? `${(r.vram_mb / 1024).toFixed(1)} GB VRAM` : '', r.ram_mb ? `${(r.ram_mb / 1024).toFixed(1)} GB RAM` : ''].filter(Boolean).join(' · ') || '—';
  const text = r => r.text ? `<details><summary class="cursor-pointer text-[11px] text-apple-tertiary">${escapeHtml(t('benchShowText'))}</summary>
      <div class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap text-[11.5px] text-apple-secondary">${escapeHtml(r.text)}</div></details>` : '';
  let head, rows;
  if (run.kind === 'asr') {
    const bw = best('wer'), bs = best('sec');
    head = ['benchColModel', 'benchColWer', 'benchColTime', 'benchColSpeed', 'benchColMemory', ''];
    rows = run.results.map(r => r.error
      ? `<tr><td class="px-2 py-1">${escapeHtml(r.name || benchModelName(r.model))}</td><td colspan="5" class="px-2 py-1 text-apple-amber">${escapeHtml(r.error)}</td></tr>`
      : `<tr class="align-top"><td class="px-2 py-1">${escapeHtml(r.name || benchModelName(r.model))}${text(r)}</td>
          ${cell(`${(r.wer * 100).toFixed(1)}%`, r.wer === bw)}${timeCell(r, r.sec === bs)}${cell(`×${r.speed}`, false)}
          <td class="px-2 py-1 text-right text-[11px] text-apple-secondary">${escapeHtml(mem(r))}<div class="text-apple-tertiary">${escapeHtml(r.device || '')}</div></td>
          <td class="px-2 py-1 text-right">${useBtn(r)}</td></tr>`);
  } else {
    const bu = best('unverified_share'), bs = best('sec');
    head = ['benchColModel', 'benchColUnverified', 'benchColTime', 'benchColPoints', ''];
    rows = run.results.map(r => r.error
      ? `<tr><td class="px-2 py-1">${escapeHtml(r.name || benchModelName(r.model))}</td><td colspan="4" class="px-2 py-1 text-apple-amber">${escapeHtml(r.error)}</td></tr>`
      : `<tr class="align-top"><td class="px-2 py-1">${escapeHtml(r.name || benchModelName(r.model))}${text(r)}</td>
          ${cell(`${(r.unverified_share * 100).toFixed(0)}% (${r.unverified})`, r.unverified_share === bu)}${timeCell(r, r.sec === bs)}${cell(r.points, false)}
          <td class="px-2 py-1 text-right">${useBtn(r)}</td></tr>`);
  }
  return `<div class="overflow-x-auto"><table class="w-full text-[12px] text-apple-label">
    <thead><tr class="text-[10.5px] uppercase tracking-wider text-apple-tertiary">${head.map((h, i) => `<th class="px-2 py-1 font-normal ${i ? 'text-right' : 'text-left'}">${h ? escapeHtml(t(h)) : ''}</th>`).join('')}</tr></thead>
    <tbody class="divide-y divide-white/5">${rows.join('')}</tbody></table></div>`
    + `<p class="text-[10.5px] text-apple-tertiary mt-1">${escapeHtml(t(run.kind === 'asr' ? 'benchAsrNote' : 'benchLlmNote'))}</p>`;
}

async function startBench() {
  const meetingId = Number(document.getElementById('bench-meeting').value);
  const models = [...(bench.picked[bench.kind] || [])].filter(id => benchModels().some(m => m.id === id));
  const res = await fetch('/api/bench/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: bench.kind, meeting_id: meetingId, models, clip_sec: Number(document.getElementById('bench-clip').value) }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  bench.data = { ...bench.data, ...data };
  renderBench();
  pollBench();
}

function pollBench() {
  clearTimeout(bench.timer);
  bench.timer = setTimeout(async () => {
    const data = await fetch('/api/bench').then(r => r.json()).catch(() => null);
    if (data) { bench.data = { ...bench.data, ...data }; renderBench(); }
    if (data && data.running && !document.getElementById('modal-settings').classList.contains('hidden')) pollBench();
  }, 1500);
}

async function cancelBench() {
  const data = await fetch('/api/bench/cancel', { method: 'POST' }).then(r => r.json()).catch(() => null);
  if (data) { bench.data = { ...bench.data, ...data }; renderBench(); }
}

async function clearBenchHistory() {
  const data = await fetch('/api/bench/history', { method: 'DELETE' }).then(r => r.json()).catch(() => null);
  if (data) { bench.data = { ...bench.data, ...data }; renderBench(); }
}

async function benchUse(kind, model) {
  if (kind === 'asr') await selectActiveWhisperModel(model);
  else await selectActiveOllamaModel(model);
}
