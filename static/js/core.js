// VerbaScribe page script, part 1 of 9: State, small helpers, dialogs and voice profiles.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

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
  if (state.appView === 'studio') { renderStudioState(); syncStudioCaptions(); }
  if (state.appView === 'ask') { renderAskScope(); renderAskThread(); }
  if (state.activeWorkspaceTab === 'info' && state.activeMeeting) loadMeetingInfo();
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
const SETTINGS_TABS = ['general', 'record', 'recognition', 'analysis', 'export', 'dub'];

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
    refreshRecordingsDirLabel();
    refreshObsidianStatus();
    state.llmProviderEdited = false;
    state.llmShowCloud = false;
    loadLlmStatus();
    setTimeout(() => drawIdleSoundCloudBars('settings-waveform-canvas'), 30);
  }
}
// Voice profiles only work with speaker separation: dimmed while it is off
function syncVoiceProfilesToggle() {
  const on = document.getElementById('set-diarization')?.checked !== false;
  const box = document.getElementById('voice-profiles-box');
  if (!box) return;
  box.style.opacity = on ? '' : '0.45';
  document.getElementById('set-voice-profiles').disabled = !on;
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
  if (tab === 'info') loadMeetingInfo();
  if (tab === 'summary') renderAnalysisEstimate();
  ['transcript', 'summary', 'speakers', 'info'].forEach(k => {
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
  hideTranslateMenu();
  const menu = document.getElementById('export-menu');
  if (menu.classList.contains('hidden')) renderExportMenu();
  menu.classList.toggle('hidden');
}

function hideExportMenu() {
  document.getElementById('export-menu')?.classList.add('hidden');
}
