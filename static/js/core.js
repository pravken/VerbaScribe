// VerbaScribe page script, part 1 of 9: State, small helpers, dialogs and voice profiles.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// VerbaScribe UI logic. Depends on vendor/marked.min.js, vendor/purify.min.js and i18n.js.
const DONE_STATUSES = ['completed', 'failed', 'cancelled', 'cleared'];

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
const ICON_SPEAKERS = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"/></svg>';

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

// A text that may not exist (a status or summary kind made up from data): undefined, not the key
function tMaybe(key) {
  return (I18N[state.uiLang] && I18N[state.uiLang][key]) || I18N.ru[key] || undefined;
}

function setUILanguage(lang) {
  state.uiLang = lang;
  document.documentElement.lang = lang;
  localStorage.setItem('whisper_ui_lang', lang);
  // The launcher's splash screen speaks the same language (see /api/ui/lang)
  fetch('/api/ui/lang', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang }) }).catch(() => {});

  // Settings -> General -> Interface language
  for (const code of ['ru', 'en']) {
    document.getElementById(`lang-btn-${code}`).className = `apple-segment-btn ${lang === code ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  }

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
    const p = state.system.paths;
    const dirEl = document.getElementById('settings-data-dir');
    if (p && dirEl) {
      dirEl.textContent = `${t('dataFolder')}: ${p.data}`;
      dirEl.title = `${t('dataFolder')}: ${p.data}\n${t('modelsFolder')}: ${p.models}`;
    }
    updateOllamaUI(state.system.ollama);
    if (state.system.models) updateWhisperModelsUI(state.system.models);
  }
  if (state.activeMeeting) {
    renderSpeakerPillsBar();
    renderSpeakersPanel();
    renderTranscript();
    renderSummaryPanel();
  }
  // Texts the scripts wrote (not data-i18n): drawn again in the new language
  loadSystemInfo().catch(() => {});
  if (typeof scheduleVramRefresh === 'function') scheduleVramRefresh(50);  // the memory badges
  if (typeof renderUpdateStatus === 'function') renderUpdateStatus();
  renderQueuePanel();
  if (state.activeMeeting) { renderTranslationControls(); updateProcessingBanner(state.activeMeeting); }
  if (typeof renderRecordSaveHint === 'function') renderRecordSaveHint();
  applyVideoLayout();
  if (typeof relabelStudioLanguages === 'function') relabelStudioLanguages();
  if (state.appView === 'studio') { renderStudioState(); syncStudioCaptions(); }
  if (state.appView === 'ask') { renderAskScope(); renderAskThread(); }
  if (state.activeWorkspaceTab === 'info' && state.activeMeeting) loadMeetingInfo();
}

// A styled yes/no question (instead of the browser's confirm): resolves true or false.
// Cancel is focused, so Enter does not confirm by accident.
function confirmDialog(title, text, okLabel, danger = true) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 modal-backdrop z-[70] flex items-center justify-center p-4';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.innerHTML = `<div class="w-full max-w-md bg-apple-card border apple-border rounded-2xl shadow-2xl p-5 space-y-4 modal-card">
      <div class="text-sm font-semibold text-white">${escapeHtml(title)}</div>
      <p class="text-xs text-apple-secondary leading-relaxed whitespace-pre-line break-words">${escapeHtml(text)}</p>
      <div class="flex justify-end gap-2">
        <button data-a="no" class="ui-btn ui-btn-subtle">${escapeHtml(t('cancel'))}</button>
        <button data-a="yes" class="ui-btn ${danger ? 'bg-apple-red text-white hover:opacity-90' : 'ui-btn-primary'}">${escapeHtml(okLabel)}</button>
      </div></div>`;
    const done = v => { wrap.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); done(false); } };
    wrap.addEventListener('click', e => {
      const a = e.target.closest('button[data-a]');
      if (e.target === wrap || (a && a.dataset.a === 'no')) done(false);
      else if (a) done(true);
    });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(wrap);
    wrap.querySelector('button[data-a="no"]').focus();
  });
}

// Short forms for the old browser dialogs (window.confirm / prompt / alert), in the app's style.
// danger: the button is red (deleting, discarding); said by the caller, not guessed from the words
function askConfirm(text, danger = false) {
  return confirmDialog(t('confirmTitle'), text, t('ok'), danger);
}

function askText(label, value = '') {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 modal-backdrop z-[70] flex items-center justify-center p-4';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.innerHTML = `<form class="w-full max-w-md bg-apple-card border apple-border rounded-2xl shadow-2xl p-5 space-y-4 modal-card">
      <label class="block text-xs text-apple-label leading-relaxed whitespace-pre-line">${escapeHtml(label)}</label>
      <input type="text" class="w-full h-9 bg-apple-sidebar border apple-border rounded-lg px-3 text-sm text-white focus:outline-none focus:border-apple-blue" />
      <div class="flex justify-end gap-2">
        <button type="button" data-a="no" class="ui-btn ui-btn-subtle">${escapeHtml(t('cancel'))}</button>
        <button type="submit" class="ui-btn ui-btn-primary">${escapeHtml(t('ok'))}</button>
      </div></form>`;
    const input = wrap.querySelector('input');
    input.value = value || '';
    const done = v => { wrap.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
    wrap.querySelector('form').addEventListener('submit', e => { e.preventDefault(); done(input.value); });
    wrap.addEventListener('click', e => { if (e.target === wrap || e.target.closest('[data-a="no"]')) done(null); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(wrap);
    input.focus();
    input.select();
  });
}

function showAlert(text) {
  return confirmDialog(t('attentionTitle'), String(text), t('ok'), false).then(() => {});
}

// A message of the server (always Russian) in the interface language (static/server_text.js)
function serverText(text) {
  if (state.uiLang !== 'en' || typeof text !== 'string' || !text || typeof SERVER_TEXT_EN === 'undefined') return text;
  if (SERVER_TEXT_EN[text]) return SERVER_TEXT_EN[text];
  for (const [ru, en] of SERVER_PATTERNS_EN) {
    const parts = ru.split('{}');
    const re = new RegExp('^' + parts.map(p => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('([\\s\\S]*?)') + '$');
    const m = text.match(re);
    if (m) return en.replace(/\{(\d)\}/g, (_, i) => serverText(m[Number(i) + 1]));
  }
  return text;
}

// What a failed answer says, for a toast: FastAPI's "detail" is a text, or a list of invalid fields
function errorText(data, res) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map(x => (x && (x.msg || x.message)) || String(x)).join('; ');
  return (typeof d === 'string' && serverText(d)) || `${t('serverError')} (HTTP ${res ? res.status : '?'})`;
}

// A lost session (the server started again with a new token, the cookie is gone): every request then
// answers 401. Instead of a page that silently stops working, the window reloads itself with a fresh
// address (in a browser the user is told to open the app again).
const _nativeFetch = window.fetch.bind(window);
let _sessionLost = false;
window.fetch = async (...args) => {
  const res = await _nativeFetch(...args);
  if (res.status === 401 && !_sessionLost && /^\/api\//.test(String(args[0]))) {
    _sessionLost = true;
    onSessionLost();
  }
  return res;
};

async function onSessionLost() {
  showToast(t('sessionLost'));
  try {
    const url = window.pywebview && window.pywebview.api && await window.pywebview.api.app_url();
    if (url && url.indexOf('token=') > 0) setTimeout(() => location.replace(url), 1500);
  } catch (e) { /* in a browser: the toast says what to do */ }
}

// The JSON of a response; a non-JSON answer (a server error page) becomes {detail: "..."} instead of
// "Unexpected token ... is not valid JSON" shown to the user
async function readJson(res) {
  const text = await res.text().catch(() => '');
  try {
    const data = text ? JSON.parse(text) : {};
    if (data && typeof data.detail === 'string') data.detail = serverText(data.detail);
    return data;
  } catch (e) {
    return { detail: `${t('serverError')} (HTTP ${res.status})` };
  }
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
const SETTINGS_TABS = ['general', 'record', 'recognition', 'analysis', 'export', 'integrations', 'dub', 'bench'];

function selectSettingsTab(name) {
  if (!SETTINGS_TABS.includes(name)) name = 'general';
  document.querySelectorAll('#modal-settings [data-stab]').forEach(el => el.classList.toggle('hidden', el.dataset.stab !== name));
  SETTINGS_TABS.forEach(k => {
    const btn = document.getElementById(`stab-${k}`);
    if (btn) btn.className = `apple-segment-btn ${k === name ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  });
  document.getElementById('modal-settings').dataset.tab = name;
  try { localStorage.setItem('verbascribe_settings_tab', name); } catch (e) {}
  if (name === 'bench' && typeof loadBench === 'function') loadBench();
}

// Keyboard: an opened window takes the focus, Tab stays inside the top one, and the focus goes back
// to where it was when the window closes
const _modalReturnFocus = {};

function focusablesIn(root) {
  return [...root.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter(el => !el.disabled && el.getClientRects().length);  // shown (offsetParent is null for fixed ones)
}

document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const open = [...document.querySelectorAll('[aria-modal="true"]')].filter(el => !el.classList.contains('hidden') && el.getClientRects().length).pop();
  if (!open) return;
  const items = focusablesIn(open);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (!open.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

// Rows and items made of <div role="button" tabindex="0">: Enter and Space click them, as a button
document.addEventListener('keydown', e => {
  if ((e.key !== 'Enter' && e.key !== ' ') || e.target.tagName === 'BUTTON' || !e.target.matches?.('[role="button"][tabindex="0"]')) return;
  e.preventDefault();
  e.target.click();
});

function openModal(name, tab) {
  const modal = document.getElementById(`modal-${name}`);
  _modalReturnFocus[name] = document.activeElement;
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.classList.remove('hidden');
  setTimeout(() => { if (!modal.contains(document.activeElement)) (focusablesIn(modal)[0] || modal).focus(); }, 60);
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
    renderVoiceProfiles(await readJson(res));
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
        <span class="text-apple-tertiary">· ${escapeHtml(t('voiceProfileSamples').replace('{n}', () => p.samples))}</span></span>
      <button onclick="deleteVoiceProfile(${Number(p.id)}, this.dataset.name)" data-name="${escapeHtml(p.name)}"
        class="ui-btn ui-btn-subtle h-6 px-2 text-[11px] text-apple-red" title="${escapeHtml(t('tipVoiceProfileDelete'))}">${escapeHtml(t('voiceProfileDelete'))}</button>
    </div>`).join('');
}

async function applyVoiceProfilesToLibrary() {
  const res = await fetch('/api/voice-profiles/apply-all', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  showToast(data.speakers ? t('voiceApplyAllDone').replace('{n}', () => data.speakers).replace('{m}', () => data.meetings) : t('voiceApplyAllNone'));
  if (data.speakers) {
    await loadMeetingsList();
    if (state.activeMeeting) selectMeeting(state.activeMeeting.id, false);
  }
}

async function deleteVoiceProfile(id, name) {
  if (!await askConfirm(t('voiceProfileDeleteConfirm').replace('{name}', () => name || ''), true)) return;
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
  const back = _modalReturnFocus[name];
  if (back && document.contains(back)) back.focus();
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
