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

// --- Export: every format in one menu. What the recording does not have yet (a translation, a
// dub, even the transcript while it is being made) is made first, and the file is then saved into
// the export folder by itself (app/pending_exports.py). ---
function exportLangChoices() {
  const m = state.activeMeeting;
  const have = Object.keys(meetingTranslations(m));
  const src = (m && m.language) || '';
  const all = (_textLangs || TEXT_LANG_FALLBACK).filter(c => c !== src && !have.includes(c))
    .map(c => ({ c, n: langName(c) })).sort((a, b) => a.n.localeCompare(b.n));
  return { have, others: all };
}

function renderExportMenu() {
  const menu = document.getElementById('export-menu');
  const m = state.activeMeeting;
  if (!m) { menu.innerHTML = ''; return; }
  const isVideo = !!m.is_video;
  const busy = !DONE_STATUSES.includes(m.status);
  const { have, others } = exportLangChoices();
  const src = (m.language || '').toLowerCase();
  if (!state.exportLang || state.exportLang === src) {
    state.exportLang = have[0] || (src === 'en' ? 'ru' : 'en');
  }
  const trLang = state.exportLang;
  const trReady = have.includes(trLang);
  const dubs = availableDubs(m).map(d => d.lang);
  const dubLangs = Object.keys(I18N.ru.dubLangNames).filter(c => c !== src);
  if (!state.exportDubLang || !dubLangs.includes(state.exportDubLang)) state.exportDubLang = dubs[0] || (src === 'en' ? 'ru' : 'en');
  const dubLang = state.exportDubLang;
  const dubReady = dubs.includes(dubLang);

  const header = (label, extra = '') =>
    `<div class="px-2.5 pt-2 pb-1 flex items-center justify-between gap-2"><span class="text-[10px] font-semibold uppercase tracking-wider text-apple-secondary">${escapeHtml(label)}</span>${extra}</div>`;
  const item = (payload, label, tag, hint) =>
    `<button onclick='runExport(${JSON.stringify(payload)})' class="macos-menu-item gap-3" ${hint ? `title="${escapeHtml(hint)}"` : ''}>`
    + `<span class="truncate">${escapeHtml(label)}</span><span class="text-[10.5px] opacity-60 shrink-0">${escapeHtml(tag)}</span></button>`;
  const note = (text, cls = 'text-apple-tertiary') => `<div class="px-2.5 pb-1 text-[10.5px] leading-snug ${cls}">${escapeHtml(text)}</div>`;
  const sep = '<div class="my-1 border-t border-white/10"></div>';
  const langSelect = (id, onchange, options) =>
    `<select id="${id}" onchange="${onchange}" onclick="event.stopPropagation()" class="h-6 max-w-[150px] bg-apple-sidebar border apple-border rounded-md px-1.5 text-[11px] text-white focus:outline-none">${options}</select>`;

  let html = '';
  if (busy) html += note(t('exportWhileProcessing'), 'text-apple-amber');
  html += header(t('exportGroupDoc'));
  html += item({ kind: 'doc', fmt: 'docx' }, 'Word', 'DOCX') + item({ kind: 'doc', fmt: 'md' }, 'Markdown', 'MD')
    + item({ kind: 'doc', fmt: 'txt' }, t('exportPlainText'), 'TXT') + item({ kind: 'doc', fmt: 'json' }, 'JSON', 'JSON');

  html += sep + header(t('exportGroupSubs'));
  html += item({ kind: 'doc', fmt: 'srt' }, t('exportSrtFile'), 'SRT');
  if (isVideo) {
    html += item({ kind: 'video_soft' }, t('exportVideoSoft'), t('exportTagVideo'), t('tipSubsSoft'))
      + item({ kind: 'video_burn' }, t('exportVideoBurn'), t('exportTagVideo'), t('tipSubsBurn'));
  }

  const trOptions = (have.length ? `<optgroup label="${escapeHtml(t('exportLangReady'))}">${have.map(c => `<option value="${c}" ${c === trLang ? 'selected' : ''}>✓ ${escapeHtml(langName(c))}</option>`).join('')}</optgroup>` : '')
    + `<optgroup label="${escapeHtml(t('exportLangNew'))}">${others.map(o => `<option value="${o.c}" ${o.c === trLang ? 'selected' : ''}>${escapeHtml(o.n)}</option>`).join('')}</optgroup>`;
  html += sep + header(t('exportGroupTr'), langSelect('export-tr-lang', 'setExportLang(this.value)', trOptions));
  if (!trReady) html += note(t('exportTrWillRun').replace('{lang}', langName(trLang)));
  const tr = (fmt, variant, label, tag) => item({ kind: 'doc', fmt, lang: trLang, variant }, label, tag);
  html += tr('docx', 'translated', 'Word', 'DOCX') + tr('md', 'translated', 'Markdown', 'MD') + tr('txt', 'translated', t('exportPlainText'), 'TXT')
    + tr('srt', 'translated', t('exportSrtFile'), 'SRT')
    + tr('docx', 'bilingual', `Word · ${t('exportBilingual')}`, 'DOCX') + tr('srt', 'bilingual', `${t('exportSrtFile')} · ${t('exportBilingual')}`, 'SRT');
  if (isVideo) {
    html += item({ kind: 'video_soft', lang: trLang, variant: 'translated' }, t('exportVideoSoftTr'), t('exportTagVideo'), t('tipSubsSoft'))
      + item({ kind: 'video_burn', lang: trLang, variant: 'translated' }, t('exportVideoBurnTr'), t('exportTagVideo'), t('tipSubsBurn'));
  }

  const dubOptions = dubLangs.map(c => `<option value="${c}" ${c === dubLang ? 'selected' : ''}>${dubs.includes(c) ? '✓ ' : ''}${escapeHtml(dubLangName(c))}</option>`).join('');
  html += sep + header(t('exportGroupDub'), langSelect('export-dub-lang', 'setExportDubLang(this.value)', dubOptions));
  if (!dubReady) html += note(t('exportDubWillRun'));
  const dubExt = dubReady ? String((availableDubs(m).find(d => d.lang === dubLang) || {}).path || '').split('.').pop().toUpperCase() : (isVideo ? 'MP4' : 'M4A');
  html += item({ kind: 'dub_media', lang: dubLang }, isVideo ? t('exportDubVideo') : t('exportDubAudio'), dubExt)
    + item({ kind: 'dub_srt', lang: dubLang }, t('exportDubSrt'), 'SRT');

  const settings = (state.system && state.system.settings) || {};
  html += sep + header('Obsidian');
  html += item({ kind: 'obsidian' }, t('exportObsidian'), 'MD', settings.obsidian_vault ? settings.obsidian_vault : t('exportObsidianNoVault'));
  if (m.obsidian_path) html += note(t('exportObsidianSaved').replace('{name}', String(m.obsidian_path).split(/[\\/]/).pop()));

  html += sep + `<button onclick="hideExportMenu(); openExportDir()" class="macos-menu-item"><span>${escapeHtml(t('exportOpenFolder'))}</span></button>`;
  menu.innerHTML = html;
}

function setExportLang(lang) {
  state.exportLang = lang;
  renderExportMenu();
}

function setExportDubLang(lang) {
  state.exportDubLang = lang;
  renderExportMenu();
}

async function runExport(item) {
  hideExportMenu();
  const m = state.activeMeeting;
  if (!m) return;
  if (item.kind === 'obsidian') { await exportToObsidian(m.id); return; }
  let res, data;
  try {
    res = await fetch(`/api/meetings/${m.id}/export-request`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    showToast(t('exportFailed'));
    return;
  }
  if (!res.ok) { showToast(data.detail || t('exportFailed')); return; }
  if (data.status === 'needs_dub') {
    state.pendingDubExport = { kind: item.kind, lang: item.lang };
    openDubModal(item.lang);
    return;
  }
  if (data.status === 'queued') {
    const what = t(`exportWaiting_${data.waiting_for}`) || '';
    showToast(t('exportQueued').replace('{what}', what).replace('{dir}', data.folder || ''), { label: t('showInFolder'), onClick: () => revealPath(data.folder) });
    await loadMeetingsList();
    return;
  }
  // Everything the export needs is there: save it now, asking where
  if (item.kind === 'doc') await exportMeetingById(m.id, item.fmt, item.lang ? { lang: item.lang, variant: item.variant } : undefined);
  else if (item.kind === 'video_soft' || item.kind === 'video_burn') await requestVideoSubtitles(item.lang || '', item.variant || 'translated', item.kind === 'video_soft' ? 'soft' : 'burn', true);
  else if (item.kind === 'dub_media') await saveDubFile(item.lang, 'media');
  else if (item.kind === 'dub_srt') await saveDubFile(item.lang, 'srt');
}

async function exportToObsidian(id) {
  const settings = (state.system && state.system.settings) || {};
  if (!settings.obsidian_vault) {
    showToast(t('exportObsidianNoVault'));
    openModal('settings', 'export');
    return;
  }
  showToast(t('obsidianSaving'));
  try {
    const res = await fetch(`/api/meetings/${id}/obsidian`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.detail || t('exportFailed')); return; }
    if (state.activeMeeting && state.activeMeeting.id === id) state.activeMeeting.obsidian_path = data.path;
    showToast(`${t('obsidianSaved')} ${data.filename}`, { label: t('showInFolder'), onClick: () => revealPath(data.path) });
  } catch (e) {
    showToast(t('exportFailed'));
  }
}

// An export that waited for a job has been written (or could not be)
function onExportedEvent(ev) {
  const files = ev.files || [];
  if (files.length) {
    const name = String(files[0]).split(/[\\/]/).pop();
    showToast(`${t('exportSavedTo')} ${name}${files.length > 1 ? ` (+${files.length - 1})` : ''}`,
      { label: t('showInFolder'), onClick: () => revealPath(files[0]) });
  } else if ((ev.queued || []).length) {
    showToast(t('subsBurnQueued').replace('{name}', ''));
  }
  if ((ev.errors || []).length) setTimeout(() => showToast(ev.errors.join('; ')), files.length ? 3000 : 0);
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
  hideFolderMenu();
  document.getElementById('export-menu')?.classList.add('hidden');
  hideTranslateMenu();
  hideTxViewMenu();
  hideRecordPresetMenu();
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
    const idleSel = document.getElementById('set-idle-unload');
    idleSel.value = [...idleSel.options].some(o => o.value === String(s.idle_unload_minutes)) ? String(s.idle_unload_minutes) : '10';
    fillObsidianSettings(s);
    fillDubSettings(s);
    renderRecordSaveHint();

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
  if (mode === 'cloud') state.llmShowCloud = true;
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
    return;
  }
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
      vad_filter: String(document.getElementById('set-vad').checked),
      audio_enhance: String(document.getElementById('set-audio-enhance')?.checked !== false),
      clean_fillers: String(document.getElementById('set-clean-fillers')?.checked !== false),
      ollama_model: document.getElementById('set-ollama-model').value.trim(),
      auto_summary: String(document.getElementById('set-auto-summary').checked),
      auto_title: String(document.getElementById('set-auto-title').checked),
      idle_unload_minutes: document.getElementById('set-idle-unload').value,
      ...obsidianSettingsPayload(),
      ...llmSettingsPayload(),
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
}

function setAskScope(scope) {
  state.askScope = scope;
  renderAskScope();
  document.getElementById('library-question').focus();
}

function askScopePayload(scope) {
  if (scope.type === 'folder') return { folder_id: scope.id };
  if (scope.type === 'meeting') return { meeting_ids: [scope.id] };
  return {};
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
    ${data.model ? `<div class="mt-2 text-[10.5px] text-apple-tertiary">${data.cloud ? '☁ ' : ''}${escapeHtml(data.cloud ? data.model : formatModelName(data.model))}${data.mode === 'full' ? ' · ' + escapeHtml(t('askModeFull')) : ''}</div>` : ''}`;
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

function fctxNewSubfolder() {
  createFolderPrompt(state.contextFolderId);
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
  return badge('transcript', m.has_transcript, t('recBadgeTranscript'))
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
          <span title="${escapeHtml(m.title)}" class="text-[12.5px] font-medium truncate">${escapeHtml(m.title)}</span>
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
      listEl.innerHTML = askHtml + `<div class="px-3 py-8 text-center text-xs text-apple-tertiary">${escapeHtml(t('noMatches'))}</div>`;
      return;
    }
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

  const folders = state.folders || [];
  if (state.meetings.length === 0 && !folders.some(f => !f.system_key)) {
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
  const items = (list, depth) => {
    if (!list || !list.length) return '';
    if (prefs.view === 'grid') {
      return `<div class="lib-grid" style="padding-left:${6 + depth * 14}px">${list.map(meetingTileHtml).join('')}</div>`;
    }
    return list.map(m => prefs.view === 'compact' ? meetingCompactRowHtml(m, depth) : meetingRowHtml(m, depth)).join('');
  };
  const collapsed = collapsedFolders();
  let html = '';
  const walk = (parentId, depth) => {
    for (const f of sortedFolders(parentId)) {
      // An empty system folder only takes room
      if (f.system_key && !meetingsInFolderTree(f.id).length && !sortedFolders(f.id).length) continue;
      html += folderRowHtml(f, depth, collapsed);
      if (collapsed.has(f.id)) continue;
      walk(f.id, depth + 1);
      html += items(byFolder.get(f.id), depth + 1);
    }
  };
  walk(null, 0);
  html += items(byFolder.get(null), 0);
  listEl.innerHTML = html;
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
  if (state.activeWorkspaceTab === 'info' && (prevId !== m.id || reloadPlayer)) loadMeetingInfo();
  if (state.appView === 'ask') renderAskScope();
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
let _recSignalAt = 0;

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

// --- Recording presets (like scenes in OBS): one click records from the chosen sources ---
// Built-in presets plus the user's own, kept in the settings (record_presets, record_default_preset)
const BUILTIN_PRESETS = [
  { id: 'call', builtin: true, source: 'both', mic: '', live: true, translate: '' },
  { id: 'note', builtin: true, source: 'mic', mic: '', live: false, translate: '' },
  { id: 'system', builtin: true, source: 'system', mic: '', live: false, translate: '' },
];

function recordPresets() {
  let own = [];
  try { own = JSON.parse((state.system && state.system.settings && state.system.settings.record_presets) || '[]'); } catch (e) {}
  return BUILTIN_PRESETS.concat(Array.isArray(own) ? own : []);
}

function defaultRecordPresetId() {
  const id = state.system && state.system.settings && state.system.settings.record_default_preset;
  return recordPresets().some(p => p.id === id) ? id : 'call';
}

function presetLabel(p) {
  return p.builtin ? t(`preset_${p.id}`) : p.name;
}

async function saveRecordPresetSettings(own, defaultId) {
  const settings = { record_presets: JSON.stringify(own) };
  if (defaultId) settings.record_default_preset = defaultId;
  const res = await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.settings && state.system) state.system.settings = data.settings;
}

function renderRecordPresetSelect(selectedId) {
  const sel = document.getElementById('record-preset-select');
  const def = defaultRecordPresetId();
  sel.innerHTML = recordPresets().map(p =>
    `<option value="${escapeHtml(p.id)}">${p.id === def ? '★ ' : ''}${escapeHtml(presetLabel(p))}</option>`).join('');
  sel.value = selectedId || def;
  const p = recordPresets().find(x => x.id === sel.value);
  document.getElementById('rec-preset-update-btn').classList.toggle('hidden', !p || !!p.builtin);
  document.getElementById('rec-preset-delete-btn').classList.toggle('hidden', !p || !!p.builtin);
  document.getElementById('rec-preset-default-btn').classList.toggle('ui-btn-active', !!p && p.id === def);
}

function applyRecordPreset(id) {
  const p = recordPresets().find(x => x.id === id) || recordPresets()[0];
  state.recPresetId = p.id;
  document.getElementById('record-source-select').value = p.source;
  const micSel = document.getElementById('record-mic-select');
  if (p.mic && [...micSel.options].some(o => o.value === p.mic)) micSel.value = p.mic;
  document.getElementById('live-captions-toggle').checked = !!p.live;
  if (p.video !== undefined) document.getElementById('record-video-select').value = p.video || '';
  document.getElementById('live-translate-select').value = p.translate || '';
  saveLiveCaptionPrefs();
  renderRecordPresetSelect(p.id);
}

function presetFromForm(base) {
  return {
    ...base,
    source: document.getElementById('record-source-select').value,
    mic: document.getElementById('record-mic-select').value || '',
    live: document.getElementById('live-captions-toggle').checked,
    translate: document.getElementById('live-translate-select').value || '',
    video: document.getElementById('record-video-select').value || '',
  };
}

async function saveRecordPresetAs() {
  const name = (prompt(t('presetNamePrompt'), '') || '').trim();
  if (!name) return;
  const own = recordPresets().filter(p => !p.builtin);
  const preset = presetFromForm({ id: `p${Date.now().toString(36)}`, name: name.slice(0, 60) });
  own.push(preset);
  await saveRecordPresetSettings(own);
  state.recPresetId = preset.id;
  renderRecordPresetSelect(preset.id);
  showToast(t('presetSaved').replace('{name}', preset.name));
}

async function updateRecordPreset() {
  const id = document.getElementById('record-preset-select').value;
  const own = recordPresets().filter(p => !p.builtin).map(p => (p.id === id ? presetFromForm(p) : p));
  await saveRecordPresetSettings(own);
  renderRecordPresetSelect(id);
  showToast(t('presetSaved').replace('{name}', presetLabel(own.find(p => p.id === id) || {})));
}

async function deleteRecordPreset() {
  const id = document.getElementById('record-preset-select').value;
  const p = recordPresets().find(x => x.id === id);
  if (!p || p.builtin || !confirm(t('presetDeleteConfirm').replace('{name}', p.name))) return;
  const own = recordPresets().filter(x => !x.builtin && x.id !== id);
  await saveRecordPresetSettings(own, defaultRecordPresetId() === id ? 'call' : null);
  applyRecordPreset(defaultRecordPresetId());
}

async function setDefaultRecordPreset() {
  const id = document.getElementById('record-preset-select').value;
  await saveRecordPresetSettings(recordPresets().filter(p => !p.builtin), id);
  renderRecordPresetSelect(id);
  showToast(t('presetDefaultSet').replace('{name}', presetLabel(recordPresets().find(p => p.id === id) || {})));
}

function toggleRecordPresetMenu(e) {
  e.stopPropagation();
  const menu = document.getElementById('record-preset-menu');
  const def = defaultRecordPresetId();
  const item = (onclick, mark, label, hint) =>
    `<button onclick="${onclick}" class="macos-menu-item gap-2" style="justify-content:flex-start">`
    + `<span class="w-3 shrink-0 text-center text-apple-amber">${mark}</span>`
    + `<span class="min-w-0"><span class="block truncate">${escapeHtml(label)}</span>`
    + (hint ? `<span class="block text-[10.5px] text-apple-tertiary truncate">${escapeHtml(hint)}</span>` : '') + `</span></button>`;
  const sourceName = { mic: t('sourceMic'), system: t('sourceSystem'), both: t('sourceBoth') };
  menu.innerHTML = recordPresets().map(p => item(
    `hideRecordPresetMenu(); quickRecord('${p.id}')`, p.id === def ? '★' : '', presetLabel(p),
    sourceName[p.source] + (p.live ? ` · ${t('presetWithCaptions')}` : ''))).join('')
    + '<div class="h-px bg-white/10 my-1"></div>'
    + item('hideRecordPresetMenu(); openRecordModal()', '', t('presetOpenSettings'), '');
  menu.classList.toggle('hidden');
}

function hideRecordPresetMenu() {
  document.getElementById('record-preset-menu')?.classList.add('hidden');
}

// Record at once with a preset (the default one without an argument)
function isRecording() {
  return !!(_mediaRecorder && _mediaRecorder.state === 'recording') || !!_recCaptureId;
}

async function quickRecord(presetId) {
  if (isRecording()) {
    switchAppView('studio');  // already recording: back to the studio
    return;
  }
  await openRecordModal(presetId || defaultRecordPresetId());
  startLiveRecording();
}

// The recording controls live in the studio tab (they used to be a separate window)
async function openRecordModal(presetId) {
  switchAppView('studio');
  await prepareStudio(presetId);
}

let _studioPrepared = false;

async function prepareStudio(presetId) {
  if (isRecording()) { renderStudioState(); return; }
  await initLiveCaptionControls();
  await populateAudioInputDevices();
  if (!_studioPrepared) {
    const savedSource = (state.system && state.system.settings && state.system.settings.record_source_mode) || 'both';
    document.getElementById('record-source-select').value = savedSource;
  }
  if (!_studioPrepared) {
    try { document.getElementById('record-video-select').value = localStorage.getItem('verbascribe_record_video') || ''; } catch (e) {}
  }
  if (!_studioPrepared || presetId) applyRecordPreset(presetId || state.recPresetId || defaultRecordPresetId());
  else renderRecordPresetSelect(state.recPresetId || defaultRecordPresetId());
  renderRecordSaveHint();

  const titleInput = document.getElementById('record-title-input');
  if (!titleInput.value.trim()) {
    const nowStr = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }).replace(':', '-');
    titleInput.value = `${t('recordDefaultTitle')} ${nowStr}`;
  }
  document.getElementById('record-timer-display').textContent = '00:00';
  document.getElementById('record-level-bar').style.width = '2%';
  document.getElementById('record-db-display').textContent = '-60 dB';
  document.getElementById('record-signal-dot').classList.remove('on');
  _studioPrepared = true;
  renderStudioState();
  setTimeout(() => drawIdleSoundCloudBars('record-waveform-canvas'), 40);
}

function saveRecordVideoPref() {
  try { localStorage.setItem('verbascribe_record_video', document.getElementById('record-video-select').value); } catch (e) {}
  renderStudioState();
}

function renderRecordSaveHint() {
  const dir = state.system && state.system.paths && state.system.paths.recordings;
  document.getElementById('record-save-hint').textContent = dir ? t('recordSaveHint').replace('{dir}', dir) : '';
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

async function initLiveCaptionControls() {
  if (liveCap.session) return;
  const prefs = liveCaptionPrefs();
  const codes = await textLanguages();
  fillTranslateSelect(codes, prefs.translate || '');
  // Speech: Whisper language codes (no script variants)
  const speech = document.getElementById('studio-speech');
  speech.innerHTML = `<option value="auto">${escapeHtml(t('studioSpeechAuto'))}</option>` + textLangOptions(codes, true);
  speech.value = studio.speech;
  document.getElementById('live-captions-toggle').checked = !!prefs.on;
  renderStudioControls();
}

// --- Quick translation languages: two buttons (right-click changes the language) and "auto",
// which translates each phrase into the other language of the two (Russian speech into English,
// English speech into Russian) ---
const QUICK_LANGS_DEFAULT = ['ru', 'en'];

function quickLangs() {
  try {
    const v = JSON.parse(localStorage.getItem('verbascribe_quick_langs') || 'null');
    if (Array.isArray(v) && v.length === 2 && v[0] !== v[1]) return v;
  } catch (e) {}
  return [...QUICK_LANGS_DEFAULT];
}

function autoPairLabel() {
  const [a, b] = quickLangs();
  return `${langCode(a)} ⇄ ${langCode(b)}`;
}

function fillTranslateSelect(codes, value) {
  const sel = document.getElementById('live-translate-select');
  sel.innerHTML = `<option value="">${escapeHtml(t('liveNoTranslation'))}</option>`
    + `<option value="auto">${escapeHtml(t('liveAutoPair').replace('{pair}', autoPairLabel()))}</option>`
    + textLangOptions(codes, false);
  sel.value = [...sel.options].some(o => o.value === value) ? value : '';
}

function renderQuickLangs() {
  const box = document.getElementById('studio-quick-langs');
  if (!box) return;
  const current = document.getElementById('live-translate-select').value;
  const [a, b] = quickLangs();
  const btn = (value, label, title, menuIndex) =>
    `<button onclick="setQuickTranslate('${value}')" ${menuIndex != null ? `oncontextmenu="openQuickLangMenu(event, ${menuIndex})"` : ''}
      class="quick-lang ${current === value ? 'on' : ''}" title="${escapeHtml(title)}">${escapeHtml(label)}</button>`;
  box.innerHTML = btn(a, langCode(a), t('tipQuickLang').replace('{lang}', langName(a)), 0)
    + btn(b, langCode(b), t('tipQuickLang').replace('{lang}', langName(b)), 1)
    + btn('auto', `⇄ ${t('liveAutoShort')}`, t('tipQuickAuto').replace('{a}', langName(a)).replace('{b}', langName(b)), null);
}

function setQuickTranslate(value) {
  const sel = document.getElementById('live-translate-select');
  sel.value = sel.value === value ? '' : value;  // a second click turns translation off
  studioConfigure();
}

function openQuickLangMenu(e, index) {
  e.preventDefault();
  e.stopPropagation();
  closeQuickLangMenu();
  const codes = (_textLangs || TEXT_LANG_FALLBACK).map(c => ({ c, n: langName(c) })).sort((x, y) => x.n.localeCompare(y.n));
  const langs = quickLangs();
  const menu = document.createElement('div');
  menu.id = 'quick-lang-menu';
  menu.className = 'fixed w-56 max-h-80 overflow-y-auto macos-menu z-50';
  menu.innerHTML = `<div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('quickLangPick'))}</div>`
    + codes.map(({ c, n }) => `<button class="macos-menu-item" data-code="${c}" ${langs.includes(c) && c !== langs[index] ? 'disabled style="opacity:.4"' : ''}>`
      + `<span>${escapeHtml(n)}</span><span class="text-[10.5px] opacity-60">${c === langs[index] ? '✓' : escapeHtml(langCode(c))}</span></button>`).join('');
  menu.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-code]');
    ev.stopPropagation();
    if (!b || b.disabled) return;
    setQuickLang(index, b.dataset.code);
    closeQuickLangMenu();
  });
  document.body.appendChild(menu);
  menu.style.left = `${Math.min(e.clientX, window.innerWidth - 236)}px`;
  menu.style.top = `${Math.min(e.clientY, window.innerHeight - 330)}px`;
}

function closeQuickLangMenu() {
  document.getElementById('quick-lang-menu')?.remove();
}

function setQuickLang(index, code) {
  const langs = quickLangs();
  const old = langs[index];
  langs[index] = code;
  try { localStorage.setItem('verbascribe_quick_langs', JSON.stringify(langs)); } catch (e) {}
  const sel = document.getElementById('live-translate-select');
  const value = sel.value === old ? code : sel.value;
  fillTranslateSelect(_textLangs || TEXT_LANG_FALLBACK, value);
  studioConfigure();
}

document.addEventListener('click', closeQuickLangMenu);

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
  liveCap.partialTranslation = '';
  liveCap.lastId = 0;
  liveCap.pcm = [];
  liveCap.pcmLen = 0;
  liveCap.error = '';
  liveCap.starting = true;
  capReset();
  syncStudioCaptions();
  try {
    const res = await fetch('/api/live/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lang: studio.speech || 'auto',
        translate_to: document.getElementById('live-translate-select').value || null,
        pair: quickLangs(),
        capture_id: _recCaptureId,
        speed: studio.speed,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Error');
    liveCap.session = data.session;
  } finally {
    liveCap.starting = false;
  }
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
  syncStudioCaptions();
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
    for (const [id, tg] of Object.entries(data.targets || {})) {
      const ln = liveCap.lines.get(Number(id));
      if (ln) ln.target = tg;
    }
    liveCap.partialTarget = data.partial_target || '';
    liveCap.partial = data.partial || '';
    liveCap.partialTranslation = data.partial_translation || '';
    liveCap.translationReady = !!data.translation_ready;
    liveCap.lang = data.lang || liveCap.lang;
    liveCap.error = data.error || '';
    syncStudioCaptions();
  } catch (e) {
    renderLiveCaptionsError(e.message);
  } finally {
    liveCap.inFlight = false;
  }
}

function renderLiveCaptionsError(msg) {
  liveCap.error = `${t('liveFailed')}: ${msg || ''}`;
  syncStudioCaptions();
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
window.trayCommand = function (cmd, arg) {
  if (cmd === 'record') {
    if (isRecording()) {
      stopAndTranscribeRecording();
      return;
    }
    // System audio is recorded by the app, so recording can start without a click in the window
    quickRecord(arg);
  } else if (cmd === 'import') {
    switchAppView('library');
    openModal('import');
  } else if (cmd === 'ask') {
    openLibraryAsk('');
  } else if (cmd === 'settings') {
    openModal('settings');
  }
};

// --- Recording studio: large captions and their translation ---
const studio = { view: 'under', speed: 'quality', font: 26, speech: 'auto', focus: false };
try {
  const saved = JSON.parse(localStorage.getItem('verbascribe_studio') || '{}');
  Object.assign(studio, {
    view: ['under', 'side', 'tr'].includes(saved.view) ? saved.view : 'under',
    speed: saved.speed || 'quality', font: saved.font || 26, speech: saved.speech || 'auto',
  });
} catch (e) {}

function saveStudioPrefs() {
  try {
    localStorage.setItem('verbascribe_studio', JSON.stringify({ view: studio.view, speed: studio.speed, font: studio.font, speech: studio.speech }));
  } catch (e) {}
}

function langCode(code) {
  return (code || '').toUpperCase();
}

// Buttons, heading and locked inputs follow whether a recording is running
function renderStudioState() {
  const rec = isRecording();
  const sourceName = { mic: t('sourceMic'), system: t('sourceSystem'), both: t('sourceBoth') };
  const p = recordPresets().find(x => x.id === state.recPresetId);
  const withVideo = document.getElementById('record-video-select').value === 'screen';
  document.getElementById('studio-source').textContent =
    [p ? presetLabel(p) : '', sourceName[document.getElementById('record-source-select').value] || '', withVideo ? t('recVideoShort') : '']
      .filter(Boolean).join(' · ');
  document.getElementById('studio-heading').textContent = t(rec ? 'studioRecording' : 'studioIdle');
  document.getElementById('studio-rec-dot').classList.toggle('studio-idle', !rec);
  for (const id of ['studio-start-btn', 'btn-start-rec']) document.getElementById(id).classList.toggle('hidden', rec);
  for (const id of ['studio-stop-btn', 'studio-cancel-btn', 'btn-stop-rec', 'btn-cancel-rec']) document.getElementById(id).classList.toggle('hidden', !rec);
  // What is recorded cannot change mid-recording; how captions look can
  for (const id of ['record-preset-select', 'record-source-select', 'record-mic-select', 'record-video-select', 'record-title-input']) {
    document.getElementById(id).disabled = rec;
  }
  document.getElementById('rec-dot-indicator').classList.toggle('animate-ping', rec);
  updateRecordButtonState();
}

function toggleStudioFocus(on) {
  studio.focus = on === undefined ? !studio.focus : !!on;
  document.getElementById('studio-view').classList.toggle('studio-focus', studio.focus);
  document.getElementById('studio-focus-label').textContent = t(studio.focus ? 'studioFocusExit' : 'studioFocus');
  if (studio.focus) {
    document.getElementById('studio-vram-chips').innerHTML = vram.html || '';
    scheduleVramRefresh(300);
  }
}

// While recording, the sidebar button and the Studio tab show the time
function updateRecordButtonState() {
  const btn = document.getElementById('record-quick-btn');
  if (!btn) return;
  const recording = isRecording();
  const timer = document.getElementById('studio-timer').textContent;
  const label = btn.querySelector('[data-i18n="recordBtn"]');
  if (recording) {
    label.textContent = `${t('studioRecordingShort')} ${timer}`;
    btn.classList.add('animate-pulse');
  } else {
    label.textContent = t('recordBtn');
    btn.classList.remove('animate-pulse');
  }
  document.getElementById('nav-studio-dot').classList.toggle('hidden', !recording);
  document.getElementById('nav-studio-icon').classList.toggle('hidden', recording);
  document.getElementById('nav-studio-label').textContent = recording ? `${t('navStudio')} · ${timer}` : t('navStudio');
}

function renderStudioControls() {
  document.getElementById('studio-fast').checked = studio.speed === 'fast';
  document.getElementById('studio-tr-options').classList.toggle('hidden', !document.getElementById('live-translate-select').value);
  renderQuickLangs();
  // "Auto" shows the language heard last
  const autoOpt = document.querySelector('#studio-speech option[value="auto"]');
  if (autoOpt) autoOpt.textContent = t('studioSpeechAuto') + (liveCap.lang && studio.speech === 'auto' ? ` · ${langCode(liveCap.lang)}` : '');
  ['under', 'side', 'tr'].forEach(k => {
    document.getElementById(`studio-view-${k}`).className = `apple-segment-btn text-[11px] ${studio.view === k ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  });
  document.getElementById('studio-captions-inner').style.setProperty('--cap-size', `${studio.font}px`);
}

function studioSetView(v) { studio.view = v; saveStudioPrefs(); renderStudioControls(); syncStudioCaptions(); }
function studioFont(d) { studio.font = Math.max(16, Math.min(56, studio.font + d)); saveStudioPrefs(); renderStudioControls(); }
function studioSetSpeed(s) { studio.speed = s; saveStudioPrefs(); renderStudioControls(); studioConfigure(); }

// Language or model changed while recording: the session translates the last lines again
async function studioConfigure(speechChanged) {
  const target = document.getElementById('live-translate-select').value || null;
  const targetChanged = target !== (studio.lastTarget ?? target);
  studio.lastTarget = target;
  studio.speech = document.getElementById('studio-speech').value || 'auto';
  saveStudioPrefs();
  saveLiveCaptionPrefs();
  renderStudioControls();
  if (targetChanged) capResetTranslations();
  if (!liveCap.session) { syncStudioCaptions(); return; }
  try {
    await fetch(`/api/live/${liveCap.session}/config`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ translate_to: target, pair: quickLangs(), speed: studio.speed, speech_lang: speechChanged ? studio.speech : null }),
    });
  } catch (e) { console.error(e); }
  syncStudioCaptions();
}

async function studioToggleCaptions(on) {
  saveLiveCaptionPrefs();
  if (on && !liveCap.session && _recAudioCtx && window._recMixedStream) {
    try { await startLiveCaptions(_recAudioCtx, window._recMixedStream); } catch (e) { renderLiveCaptionsError(e.message); }
  } else if (!on) {
    stopLiveCaptions();
  }
  syncStudioCaptions();
}

// --- Studio captions without flicker ---
// Only final text is shown: the server revises the phrase being spoken two or three times, and
// showing each revision looked like the same line appearing again and again. The phrase being
// spoken is a quiet "…" row; when it becomes final its text fades in once, and its translation
// fades in under it when it is ready. Every line keeps its own element while it is shown.
const CAP_MAX_ROWS = 150;
const capView = { rows: new Map(), draftKey: null, draftSeq: 0, stick: true };

class CapTyper {
  constructor(el) { this.el = el; this.target = ''; }

  set(text) {
    text = text || '';
    if (text === this.target) return;
    this.target = text;
    this.el.textContent = text;
    if (text) {
      this.el.classList.remove('cap-in');
      void this.el.offsetWidth;  // restarts the fade
      this.el.classList.add('cap-in');
    }
  }

  jump() { this.el.textContent = this.target; }
}

function capFollowBottom() {
  if (!capView.stick) return;
  const box = document.getElementById('studio-captions');
  box.scrollTop = box.scrollHeight;
}

function capReset() {
  capView.rows.clear();
  capView.draftKey = null;
  document.getElementById('studio-cap-rows').innerHTML = '';
  capView.stick = true;
}

function capResetTranslations() {
  for (const row of capView.rows.values()) {
    row.tr.target = '';
    row.tr.jump();
  }
}

function createCapRow(key) {
  const el = document.createElement('div');
  el.className = 'cap-row';
  el.innerHTML = '<div class="cap-o"><span class="lang-tag cap-o-tag"></span><span class="cap-orig"></span></div>'
    + '<div class="cap-t"><span class="lang-tag cap-t-tag"></span><span class="cap-tr"></span></div>';
  document.getElementById('studio-cap-rows').appendChild(el);
  const row = { key, el, orig: new CapTyper(el.querySelector('.cap-orig')), tr: new CapTyper(el.querySelector('.cap-tr')) };
  capView.rows.set(key, row);
  return row;
}

function setCapTags(row, lang, target) {
  const o = row.el.querySelector('.cap-o-tag');
  const tt = row.el.querySelector('.cap-t-tag');
  if (o.textContent !== langCode(lang)) o.textContent = langCode(lang);
  if (tt.textContent !== langCode(target)) tt.textContent = langCode(target);
}

function syncStudioCaptions() {
  const target = document.getElementById('live-translate-select').value;
  const view = target ? studio.view : 'orig';
  const rowsBox = document.getElementById('studio-cap-rows');
  const viewCls = `cap-rows cap-view-${view}`;
  if (rowsBox.className !== viewCls) rowsBox.className = viewCls;

  const lines = [...liveCap.lines.values()].sort((a, b) => a.id - b.id);
  const fresh = lines.filter(ln => !capView.rows.has(ln.id));
  fresh.forEach((ln, i) => {
    const draftRow = i === 0 && capView.draftKey ? capView.rows.get(capView.draftKey) : null;
    if (draftRow) {
      capView.rows.delete(capView.draftKey);
      capView.draftKey = null;
      draftRow.key = ln.id;
      capView.rows.set(ln.id, draftRow);
      draftRow.el.classList.remove('cap-draft');
    } else {
      createCapRow(ln.id);
    }
  });
  for (const ln of lines) {
    const row = capView.rows.get(ln.id);
    if (!row) continue;
    row.orig.set(ln.text);
    row.el.classList.toggle('cap-same', !!ln.translation && ln.translation === ln.text);
    row.tr.set(ln.translation || '');
    setCapTags(row, ln.lang, ln.target || (target === 'auto' ? '' : target));
  }

  if (liveCap.partial) {
    let row = capView.draftKey ? capView.rows.get(capView.draftKey) : null;
    if (!row) {
      capView.draftKey = `d${++capView.draftSeq}`;
      row = createCapRow(capView.draftKey);
      row.el.classList.add('cap-draft');
    }
    setCapTags(row, liveCap.lang, liveCap.partialTarget || (target === 'auto' ? '' : target));
  } else if (capView.draftKey) {
    // The draft ended without becoming a line (noise taken for speech)
    capView.rows.get(capView.draftKey)?.el.remove();
    capView.rows.delete(capView.draftKey);
    capView.draftKey = null;
  }

  // Only the most recent lines stay in the document
  for (const [key, row] of capView.rows) {
    if (capView.rows.size <= CAP_MAX_ROWS) break;
    if (key === capView.draftKey) continue;
    row.el.remove();
    capView.rows.delete(key);
  }

  const has = capView.rows.size > 0;
  const head = document.getElementById('studio-cap-head');
  head.classList.toggle('hidden', !(view === 'side' && has));
  if (view === 'side') {
    document.getElementById('studio-cap-head-orig').textContent = t('studioOriginalCol') + (liveCap.lang ? ` · ${langName(liveCap.lang)}` : '');
    document.getElementById('studio-cap-head-tr').textContent = target === 'auto' ? `${t('studioTranslationCol')} · ${autoPairLabel()}` : langName(target);
  }
  const empty = document.getElementById('studio-cap-empty');
  empty.classList.toggle('hidden', has);
  if (!has) {
    empty.textContent = liveCap.starting ? t('liveStarting')
      : liveCap.session ? t('liveListening')
      : isRecording() ? t('studioCaptionsOff')
      : t('studioIdleHint');
  }
  capFollowBottom();
  renderStudioControls();
  const status = document.getElementById('studio-status');
  status.textContent = liveCap.error ? liveCap.error
    : target && liveCap.session && !liveCap.translationReady ? t('studioLoadingTranslator') : '';
  status.className = `text-[11px] ml-auto ${liveCap.error ? 'text-apple-amber' : 'text-apple-tertiary'}`;
}

async function startLiveRecording() {
  if (isRecording()) return;
  try {
    const sourceMode = document.getElementById('record-source-select').value || 'mic';
    const micId = document.getElementById('record-mic-select').value || '';
    syncRecordModalToSettings();

    // Video first: the screen picker must open right after the click, before anything is awaited
    _recVideoStream = null;
    if (document.getElementById('record-video-select').value === 'screen') {
      try {
        _recVideoStream = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: { ideal: 30, max: 30 }, width: { max: 1920 }, height: { max: 1080 } },
          audio: false,
        });
      } catch (e) {
        if (e && e.name === 'NotAllowedError') return;  // the picker was closed: nothing starts
        throw new Error(t('recVideoFailed') + (e && e.message ? `: ${e.message}` : ''));
      }
      const preview = document.getElementById('record-video-preview');
      preview.srcObject = _recVideoStream;
      preview.classList.remove('hidden');
      // Sharing stopped from the system bar: the sound keeps recording, the picture ends there
      _recVideoStream.getVideoTracks()[0].addEventListener('ended', () => {
        preview.classList.add('hidden');
        showToast(t('recVideoEnded'));
      });
    }

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

    // The page records the microphone and the video; with system audio from the app, no
    // microphone and no video there is nothing for it to record
    _mediaRecorder = null;
    _recPage = null;
    const pageAudio = !(_recCaptureId && sourceMode === 'system');
    if (pageAudio || _recVideoStream) {
      const tracks = [...(_recVideoStream ? _recVideoStream.getVideoTracks() : []), ...(pageAudio ? mixedStream.getAudioTracks() : [])];
      const { mimeType, options } = recorderFormat(!!_recVideoStream, pageAudio);
      const res = await fetch('/api/record/page/start', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Error');
      _recPage = { id: data.page_id, upload: Promise.resolve(), failed: '', hasVideo: !!_recVideoStream, hasMic: pageAudio };
      _mediaRecorder = new MediaRecorder(new MediaStream(tracks), mimeType ? { mimeType, ...options } : options);
      _mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) sendRecordingChunk(_recPage, e.data);
      };
      _mediaRecorder.start(1000);
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
    liveCap.lines = new Map();
    liveCap.partial = '';
    liveCap.error = '';
    capReset();
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

      if (state.appView === 'studio') drawLiveSoundCloudBars('record-waveform-canvas', _recWaveHistory, '#FF453A');

      const pct = Math.min(100, Math.max(2, Math.round(((db + 60) / 60) * 100)));
      const levelCls = db > -6
        ? 'h-full bg-apple-red transition-all duration-75'
        : (db > -42 ? 'h-full bg-apple-green transition-all duration-75' : 'h-full bg-apple-secondary transition-all duration-75');
      for (const id of ['record-level-bar', 'studio-level']) {
        const bar = document.getElementById(id);
        bar.style.width = `${pct}%`;
        bar.className = levelCls;
      }
      document.getElementById('record-db-display').textContent = `${db} dB`;
      document.getElementById('studio-db').textContent = `${db} dB`;

      // A plain on/off dot with a short hold, so speech pauses do not make it blink
      const now = performance.now();
      if (db > -44) _recSignalAt = now;
      const on = now - _recSignalAt < 600;
      const dot = document.getElementById('record-signal-dot');
      if (dot.classList.contains('on') !== on) dot.classList.toggle('on', on);

      _recAnimFrame = requestAnimationFrame(updateMeter);
    }
    updateMeter();

    _recStartTime = Date.now();
    clearInterval(_recTimerInterval);
    _recTimerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - _recStartTime) / 1000);
      document.getElementById('record-timer-display').textContent = formatTime(elapsed);
      document.getElementById('studio-timer').textContent = formatTime(elapsed);
      updateRecordButtonState();
    }, 400);

    reportRecordingState(true);
    window._recMixedStream = mixedStream;
    switchAppView('studio');
    renderStudioState();
    syncStudioCaptions();
    document.getElementById('record-status-hint').textContent = t('recStatusRecording');
  } catch (err) {
    _cleanupRecordingResources();
    alert(t('recCaptureError') + err.message);
  }
}

// The page's recording goes to the server every second, in order (see /api/record/page)
let _recPage = null;
let _recVideoStream = null;

function recorderFormat(video, audio) {
  const candidates = video
    ? (audio ? ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'] : ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'])
    : ['audio/webm;codecs=opus', 'audio/webm'];
  const mimeType = candidates.find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
  return { mimeType, options: video ? { videoBitsPerSecond: 2_500_000, audioBitsPerSecond: 128_000 } : { audioBitsPerSecond: 128_000 } };
}

function sendRecordingChunk(page, blob) {
  if (!page) return;
  page.upload = page.upload.then(async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const r = await fetch(`/api/record/page/${page.id}/chunk`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: blob });
        if (r.ok) return;
      } catch (e) {}
      await new Promise(res => setTimeout(res, 500 * (attempt + 1)));
    }
    page.failed = t('recChunkFailed');
  });
}

function _cleanupRecordingResources() {
  reportRecordingState(false);
  window._recMixedStream = null;
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
  if (_recVideoStream) {
    _recVideoStream.getTracks().forEach(tr => tr.stop());
    _recVideoStream = null;
  }
  const preview = document.getElementById('record-video-preview');
  preview.srcObject = null;
  preview.classList.add('hidden');
  if (_recAudioCtx) {
    try { _recAudioCtx.close(); } catch (e) {}
    _recAudioCtx = null;
  }
  _mediaRecorder = null;
  document.getElementById('studio-timer').textContent = '00:00';
  document.getElementById('studio-level').style.width = '2%';
  document.getElementById('record-status-hint').textContent = t('recordWaveHint');
  // The next recording gets a fresh default title; the captions stay readable until then
  document.getElementById('record-title-input').value = '';
  if (studio.focus) toggleStudioFocus(false);
  renderStudioState();
  syncStudioCaptions();
  setTimeout(() => drawIdleSoundCloudBars('record-waveform-canvas'), 0);
}

function cancelLiveRecording() {
  if (!isRecording()) return;
  if (!confirm(t('recCancelConfirm'))) return;
  if (_mediaRecorder && _mediaRecorder.state !== 'inactive') {
    _mediaRecorder.ondataavailable = null;
    _mediaRecorder.onstop = null;
    _mediaRecorder.stop();
  }
  if (_recPage) {
    const page = _recPage;
    page.upload.then(() => fetch(`/api/record/page/${page.id}/cancel`, { method: 'POST' })).catch(() => {});
    _recPage = null;
  }
  if (_recCaptureId) {
    fetch(`/api/record/${_recCaptureId}/cancel`, { method: 'POST' }).catch(() => {});
    _recCaptureId = null;
  }
  _cleanupRecordingResources();
  prepareStudio();
}

async function stopAndTranscribeRecording() {
  if (!_mediaRecorder && !_recCaptureId) return;
  document.getElementById('record-status-hint').textContent = t('recStatusSaving');

  const recorder = _mediaRecorder;
  if (recorder && recorder.state !== 'inactive') {
    await new Promise(resolve => {
      recorder.onstop = resolve;
      recorder.stop();  // the last chunk arrives before onstop
    });
  }
  const page = _recPage;
  _recPage = null;
  const captureId = _recCaptureId;
  _recCaptureId = null;
  const rawTitle = (document.getElementById('record-title-input').value || t('recordDefaultTitle')).trim();
  _cleanupRecordingResources();

  if (page) await page.upload;  // every chunk is on the server
  if (page && page.failed) showToast(page.failed);
  const res = await fetch('/api/record/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: rawTitle, page_id: page ? page.id : null, capture_id: captureId,
      has_video: !!(page && page.hasVideo), has_mic: !!(page && page.hasMic),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || 'Error');
    return;
  }
  const created = data.created;
  await loadMeetingsList();
  prepareStudio();
  const first = created && created[0];
  showToast(
    t('recordSavedQueued'),
    first ? { label: t('recordOpen'), title: first.media_path || '', onClick: () => { switchAppView('library'); selectMeeting(first.id, true); } } : undefined
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
    if (top && !isTypingTarget(e.target)) {
      e.preventDefault();
      closeModal(top.id.slice('modal-'.length));
    } else if (!top && studio.focus) {
      e.preventDefault();
      toggleStudioFocus(false);
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
  const hasMedia = state.appView === 'library' && !!state.activeMeeting && !!player && !!player.src;
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
  } else if (e.code === 'KeyF' && !e.shiftKey && state.hasVideo && state.appView === 'library') {
    e.preventDefault();
    toggleTheatre();
  } else if (e.code === 'KeyR' && !e.shiftKey) {
    e.preventDefault();
    quickRecord();
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

// --- Updates (app/updater.py): check GitHub Releases, download, verify, install on request ---
const updateState = { info: null, notified: false, polling: false };

function renderUpdateStatus() {
  const info = updateState.info;
  const text = document.getElementById('update-status-text');
  const btn = document.getElementById('update-install-btn');
  if (!text || !info) return;
  const en = state.uiLang === 'en';
  const dl = info.download || {};
  let msg = (en ? 'Installed version ' : 'Установлена версия ') + info.current + '.';
  if (dl.active) msg += en ? ` Downloading ${info.latest.version}… ${dl.progress}%` : ` Скачивание ${info.latest.version}… ${dl.progress}%`;
  else if (dl.error) msg += ' ' + dl.error;
  else if (info.available) {
    msg += en ? ` Version ${info.latest.version} is available.` : ` Доступна версия ${info.latest.version}.`;
    if (info.git_checkout) msg += en ? ' This folder is a git checkout: update it with git pull.' : ' Папка программы — git-репозиторий: обновите её командой git pull.';
  } else if (info.error) msg += (en ? ' Check failed: ' : ' Не удалось проверить: ') + info.error;
  else if (info.checked_at) msg += en ? ' This is the latest version.' : ' Это последняя версия.';
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
  const en = state.uiLang === 'en';
  if (!force && info.available && !updateState.notified) {
    updateState.notified = true;
    showToast(en ? `VerbaScribe ${info.latest.version} is available` : `Доступна версия VerbaScribe ${info.latest.version}`,
      { label: en ? 'Details' : 'Подробнее', onClick: () => openModal('settings', 'general') });
  } else if (force && !info.available && !info.error) {
    showToast(en ? 'You have the latest version' : 'У вас последняя версия');
  }
}

async function installUpdate() {
  const info = updateState.info;
  if (!info || !info.can_install) return;
  const en = state.uiLang === 'en';
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
  const ok = confirm(en
    ? `Install VerbaScribe ${info.latest.version}? The app will close, update itself and start again.`
    : `Установить VerbaScribe ${info.latest.version}? Программа закроется, обновится и запустится снова.`);
  if (!ok) return;
  const r = await fetch('/api/update/install', { method: 'POST' });
  if (!r.ok) { showToast((await r.json()).detail || 'Error'); return; }
  showToast(en ? 'Installing the update…' : 'Установка обновления…');
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
  loadLlmStatus();
  setTimeout(() => checkForUpdates(false), 4000);  // after the page has settled; only if enabled in settings
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

  renderMarkdownWithTimestamps(box, md, state.activeMeeting.duration);
}

// LLM output is derived from the recording's speech, so it is treated as untrusted HTML
function sanitizedMarkdown(md) {
  return DOMPurify.sanitize(marked.parse(md || ''), { FORBID_TAGS: ['style', 'form', 'input'], FORBID_ATTR: ['style'] });
}

function renderMarkdownWithTimestamps(box, md, durationSec) {
  box.innerHTML = sanitizedMarkdown(md);
  linkifyTimestamps(box, Number(durationSec) || 0, (sec) => seekPlayer(sec));
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

async function generateSummaryForActiveTab() {
  if (!state.activeMeeting) return;
  const btn = document.getElementById('generate-summary-btn');
  const box = document.getElementById('summary-content');
  const { chosenModel, prettyModel } = summaryModelChoice();
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

  const { chosenModel, prettyModel } = summaryModelChoice();
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

// --- Folder for recordings made in the app ---
async function refreshRecordingsDirLabel() {
  try {
    const data = await (await fetch('/api/recordings-dir')).json();
    const el = document.getElementById('set-recordings-dir');
    el.textContent = data.folder || '';
    el.title = data.folder || '';
    document.getElementById('recdir-reset-btn').classList.toggle('hidden', !data.folder || data.folder === data.default);
    const uploads = state.system && state.system.paths && state.system.paths.uploads;
    document.getElementById('set-imports-dir').textContent = uploads ? t('recDirImports').replace('{dir}', uploads) : '';
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
  };
  for (const k of OBSIDIAN_TOGGLES) out[`obsidian_${k}`] = String(document.getElementById(`set-obsidian-${k}`).checked);
  return out;
}

function updateObsidianPreview() {
  const vault = document.getElementById('set-obsidian-vault').dataset.path || '';
  const sub = document.getElementById('set-obsidian-subfolder').value.trim();
  const tpl = document.getElementById('set-obsidian-filename').value.trim() || '{date} {title}';
  const m = state.activeMeeting;
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const name = tpl.replace('{date}', date).replace('{time}', `${String(d.getHours()).padStart(2, '0')}-${String(d.getMinutes()).padStart(2, '0')}`)
    .replace('{title}', (m && m.title) || t('obsExampleTitle')).replace('{lang}', ((m && m.language) || 'ru').toUpperCase());
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

// --- "Details" tab: where the file is, what it is, and what was done with it ---
async function loadMeetingInfo() {
  const box = document.getElementById('info-content');
  const m = state.activeMeeting;
  if (!m) return;
  const id = m.id;
  box.innerHTML = `<div class="py-10 text-center text-xs text-apple-secondary animate-pulse">…</div>`;
  try {
    const res = await fetch(`/api/meetings/${id}/info`);
    const info = await res.json();
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
    <button onclick="revealPath(this.dataset.p)" data-p="${escapeHtml(p)}" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('showInFolder'))}</button>
    <button onclick="copyText(this.dataset.p)" data-p="${escapeHtml(p)}" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('copyPath'))}</button>` : '';

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
    row(t('infoStatus'), t(`status_${info.status}`) || info.status)
    + row(t('infoModel'), formatModelName(info.model_name))
    + row(t('infoLanguage'), info.language ? `${langName(info.language)} (${String(info.language).toUpperCase()})` : '')
    + row(t('infoCounts'), t('infoCountsValue').replace('{spk}', info.speakers).replace('{seg}', info.segments).replace('{words}', info.words))
    + row(t('infoAnalyses'), (info.summaries || []).map(k => t(`summaryName_${k}`) || k).join(', '))
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
      <span>${escapeHtml(t('infoPendingExports').replace('{n}', info.pending_exports.length))}</span>
      <button onclick="cancelPendingExports(${info.id})" class="ui-btn ui-btn-subtle h-6 px-2 text-[11px]">${escapeHtml(t('cancel'))}</button>
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
  openLibraryAsk(t('ctxAskTemplate').replace('{text}', text.length > 300 ? text.slice(0, 300) + '…' : text), scope);
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
    showToast(t('ctxAddedToDictionary').replace('{term}', term));
    loadDictionary();
  }
}

document.addEventListener('contextmenu', (e) => {
  closeTextMenu();
  if (e.defaultPrevented) return;
  const items = textMenuItems(e);
  if (!items || !items.some(i => !i.sep)) return;
  e.preventDefault();
  closeQuickLangMenu();
  const menu = document.createElement('div');
  menu.id = 'text-menu';
  menu.className = 'fixed min-w-[220px] macos-menu z-[60]';
  const clean = items.filter((it, i, a) => !it.sep || (i > 0 && !a[i - 1].sep && i < a.length - 1));
  menu.innerHTML = clean.map((it, i) => it.sep
    ? '<div class="my-1 h-px bg-white/10"></div>'
    : `<button class="macos-menu-item gap-6" data-i="${i}" ${it.disabled ? 'disabled style="opacity:.4"' : ''}><span>${escapeHtml(it.label)}</span>`
      + `<span class="text-[10.5px] opacity-50 font-mono">${escapeHtml(it.key || '')}</span></button>`).join('');
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
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(e.clientX, window.innerWidth - r.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(e.clientY, window.innerHeight - r.height - 4))}px`;
});
document.addEventListener('click', closeTextMenu);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTextMenu(); });
window.addEventListener('blur', closeTextMenu);
document.addEventListener('scroll', closeTextMenu, true);
