// VerbaScribe page script, part 2 of 9: The export menu and the recording context menu.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

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
    `<button onclick="runExport(${jsArg(payload)})" class="macos-menu-item gap-3" ${hint ? `title="${escapeHtml(hint)}"` : ''}>`
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
  html += item({ kind: 'doc', fmt: 'srt' }, t('exportSrtFile'), 'SRT') + item({ kind: 'doc', fmt: 'vtt' }, t('exportVttFile'), 'VTT');
  if (isVideo) {
    html += item({ kind: 'video_soft' }, t('exportVideoSoft'), t('exportTagVideo'), t('tipSubsSoft'))
      + item({ kind: 'video_burn' }, t('exportVideoBurn'), t('exportTagVideo'), t('tipSubsBurn'));
  }

  const trOptions = (have.length ? `<optgroup label="${escapeHtml(t('exportLangReady'))}">${have.map(c => `<option value="${c}" ${c === trLang ? 'selected' : ''}>✓ ${escapeHtml(langName(c))}</option>`).join('')}</optgroup>` : '')
    + `<optgroup label="${escapeHtml(t('exportLangNew'))}">${others.map(o => `<option value="${o.c}" ${o.c === trLang ? 'selected' : ''}>${escapeHtml(o.n)}</option>`).join('')}</optgroup>`;
  html += sep + header(t('exportGroupTr'), langSelect('export-tr-lang', 'setExportLang(this.value)', trOptions));
  if (!trReady) html += note(t('exportTrWillRun').replace('{lang}', () => langName(trLang)));
  const tr = (fmt, variant, label, tag) => item({ kind: 'doc', fmt, lang: trLang, variant }, label, tag);
  html += tr('docx', 'translated', 'Word', 'DOCX') + tr('md', 'translated', 'Markdown', 'MD') + tr('txt', 'translated', t('exportPlainText'), 'TXT')
    + tr('srt', 'translated', t('exportSrtFile'), 'SRT') + tr('vtt', 'translated', t('exportVttFile'), 'VTT')
    + tr('docx', 'bilingual', `Word · ${t('exportBilingual')}`, 'DOCX') + tr('srt', 'bilingual', `${t('exportSrtFile')} · ${t('exportBilingual')}`, 'SRT')
    + tr('vtt', 'bilingual', `${t('exportVttFile')} · ${t('exportBilingual')}`, 'VTT');
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

  // A shorter copy of the recording (app/media_cut.py)
  html += sep + header(t('exportGroupCut'));
  if (!(m.segments || []).length) html += note(t('exportCutNeedsText'));
  else html += item({ kind: 'cut_clean' }, t('exportCutClean'), isVideo ? 'MP4' : 'M4A', t('tipCutClean'))
    + item({ kind: 'cut_highlights' }, t('exportCutHighlights'), isVideo ? 'MP4' : 'M4A', t('tipCutHighlights'))
    + item({ kind: 'cut_denoise' }, t('exportCutDenoise'), isVideo ? 'MP4' : 'M4A', t('tipCutDenoise'));

  const settings = (state.system && state.system.settings) || {};
  html += sep + header('Obsidian');
  html += item({ kind: 'obsidian' }, t('exportObsidian'), 'MD', settings.obsidian_vault ? settings.obsidian_vault : t('exportObsidianNoVault'));
  if (m.obsidian_path) html += note(t('exportObsidianSaved').replace('{name}', () => String(m.obsidian_path).split(/[\\/]/).pop()));

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

async function ctxClearResults() {
  hideContextMenu();
  const id = state.contextMeetingId;
  const m = state.meetings.find(x => x.id === id);
  if (!m || !(await confirmDialog(t('clearResultsTitle'), t('clearResultsText').replace('{title}', () => m.title), t('ctxClearResults')))) return;
  const res = await fetch(`/api/meetings/${id}/clear-results`, { method: 'POST' });
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
  await loadMeetingsList();
  if (state.activeMeeting && state.activeMeeting.id === id) await selectMeeting(id, true);
}

async function ctxRemoveMedia() {
  hideContextMenu();
  const id = state.contextMeetingId;
  const m = state.meetings.find(x => x.id === id);
  if (!m || !(await confirmDialog(t('removeMediaTitle'), t('removeMediaText').replace('{title}', () => m.title), t('ctxRemoveMedia')))) return;
  const res = await fetch(`/api/meetings/${id}/remove-media`, { method: 'POST' });
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
  await loadMeetingsList();
  if (state.activeMeeting && state.activeMeeting.id === id) await selectMeeting(id, true);
}

async function ctxCut(kind) {
  hideContextMenu();
  const id = state.contextMeetingId;
  if (!id) return;
  if (!state.activeMeeting || state.activeMeeting.id !== id) await selectMeeting(id, true);
  requestCut(kind);
}

async function requestCut(kind) {
  const m = state.activeMeeting;
  if (!m) return;
  if (kind === 'highlights') showToast(t('cutPicking'));
  const res = await fetch(`/api/meetings/${m.id}/cut`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showToast(data.detail || 'Error'); return; }
  if (data.cancelled) return;
  const saved = Math.max(0, Math.round((data.duration || 0) - (data.kept_seconds || 0)));
  showToast(t('cutQueued').replace('{name}', () => data.filename).replace('{kept}', () => formatTime(data.kept_seconds || 0))
    + (kind === 'clean' && saved ? ` · −${formatTime(saved)}` : ''));
  await loadMeetingsList();
}

async function runExport(item) {
  hideExportMenu();
  const m = state.activeMeeting;
  if (!m) return;
  if (item.kind === 'obsidian') { await exportToObsidian(m.id); return; }
  if (item.kind === 'cut_clean' || item.kind === 'cut_highlights' || item.kind === 'cut_denoise') {
    await requestCut({ cut_clean: 'clean', cut_highlights: 'highlights', cut_denoise: 'denoise' }[item.kind]);
    return;
  }
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
    showToast(t('exportQueued').replace('{what}', () => what).replace('{dir}', () => data.folder || ''), { label: t('showInFolder'), onClick: () => revealPath(data.folder) });
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
    showToast(t('subsBurnQueued').replace('{name}', () => ''));
  }
  if ((ev.errors || []).length) setTimeout(() => showToast(ev.errors.join('; ')), files.length ? 3000 : 0);
}

// --- Right-Click Context Menu on Sidebar Recording Items ---
function openRecordingContextMenu(e, meetingId) {
  e.preventDefault();
  e.stopPropagation();
  document.getElementById('export-menu')?.classList.add('hidden');

  // Right-click on one of several selected recordings: the actions for all of them
  if (state.multi && state.multi.size > 1 && state.multi.has(meetingId)) { openBulkMenu(e); return; }
  if (state.multi && state.multi.size) clearLibSelection();
  state.contextMeetingId = meetingId;
  const m = state.meetings.find(x => x.id === meetingId);
  if (!m) return;

  document.getElementById('ctx-title-header').textContent = m.title;
  document.getElementById('ctx-cancel-item').classList.toggle('hidden', DONE_STATUSES.includes(m.status));
  const hasBackup = state.activeMeeting && state.activeMeeting.id === m.id && state.activeMeeting.has_segments_backup;
  document.getElementById('ctx-restore-item').classList.toggle('hidden', !hasBackup);
  // Inherited from a folder: shown as on, and changed on that folder
  const inherited = !m.local_only && folderLocalOnly(m.folder_id);
  document.getElementById('ctx-local-item-check').textContent = m.local_only || inherited ? '✓' : '';
  document.getElementById('ctx-series-name').textContent = ((state.series || []).find(s => s.id === m.series_id) || {}).name || '';
  document.getElementById('ctx-local-item').disabled = inherited;
  document.getElementById('ctx-local-item').style.opacity = inherited ? '.5' : '';
  document.getElementById('ctx-local-item').title = inherited ? t('tipLocalOnlyInherited') : t('tipLocalOnly');
  const menu = document.getElementById('recording-context-menu');
  menu.classList.remove('hidden');

  const x = Math.min(e.clientX, window.innerWidth - 235);
  const y = Math.min(e.clientY, window.innerHeight - 265);
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
}

function openBulkMenu(e) {
  document.getElementById('bulk-menu')?.remove();
  const menu = document.createElement('div');
  menu.id = 'bulk-menu';
  menu.className = 'fixed w-56 macos-menu z-50';
  const item = (fn, label, cls = '') => `<button class="macos-menu-item ${cls}" data-fn="${fn}"><span>${escapeHtml(label)}</span></button>`;
  menu.innerHTML = `<div class="px-2.5 py-1 text-[11px] font-semibold text-apple-secondary border-b border-white/10 mb-1">${escapeHtml(t('libSelected').replace('{n}', () => state.multi.size))}</div>`
    + item('bulkRename', t('bulkRename') + '…') + item('bulkAutoTitles', t('bulkAutoTitle'))
    + '<div class="my-1 h-px bg-white/10"></div>' + item('bulkDelete', t('ctxDelete'), 'text-apple-red');
  menu.addEventListener('click', ev => {
    const b = ev.target.closest('button[data-fn]');
    if (!b) return;
    menu.remove();
    window[b.dataset.fn]();
  });
  document.body.appendChild(menu);
  menu.style.left = `${Math.min(e.clientX, window.innerWidth - 232)}px`;
  menu.style.top = `${Math.min(e.clientY, window.innerHeight - 140)}px`;
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 0);
}

// "Local analysis only" set on a folder covers everything inside it, at any depth
function folderLocalOnly(folderId) {
  const seen = new Set();
  let f = folderById(folderId);
  while (f && !seen.has(f.id)) {
    if (f.local_only) return true;
    seen.add(f.id);
    f = folderById(f.parent_id);
  }
  return false;
}

function meetingLocalOnly(m) {
  return !!m && (m.local_only || folderLocalOnly(m.folder_id));
}

async function ctxToggleLocalOnly() {
  hideContextMenu();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  if (!m) return;
  const res = await fetch(`/api/meetings/${m.id}/local-only`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ local_only: !m.local_only }),
  });
  if (!res.ok) return;
  m.local_only = !m.local_only;
  if (state.activeMeeting && state.activeMeeting.id === m.id) {
    state.activeMeeting.local_only = m.local_only;
    renderAnalysisEstimate();
  }
  showToast(m.local_only ? t('localOnlyOn') : t('localOnlyOff'));
  renderMeetingsSidebar();
}

// --- Series menu of a recording ---
state.series = [];

async function loadSeries() {
  state.series = await fetch('/api/series').then(r => r.ok ? r.json() : []).catch(() => []);
}

async function openSeriesMenu(e) {
  e.stopPropagation();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  hideContextMenu();
  if (!m) return;
  await loadSeries();
  document.getElementById('series-menu')?.remove();
  const menu = document.createElement('div');
  menu.id = 'series-menu';
  menu.className = 'fixed w-60 max-h-80 overflow-y-auto macos-menu z-50';
  menu.innerHTML = `<div class="px-2.5 py-1 text-[10.5px] text-apple-tertiary">${escapeHtml(t('ctxSeries'))}</div>`
    + state.series.map(s => `<button class="macos-menu-item" data-sid="${s.id}"><span class="truncate">${escapeHtml(s.name)}</span><span class="text-[10.5px] opacity-70">${m.series_id === s.id ? '✓' : s.count}</span></button>`).join('')
    + `<button class="macos-menu-item" data-sid="new"><span>${escapeHtml(t('seriesNew'))}</span></button>`
    + (m.series_id ? `<button class="macos-menu-item" data-sid="none"><span>${escapeHtml(t('seriesRemove'))}</span></button>` : '');
  menu.addEventListener('click', async (ev) => {
    const b = ev.target.closest('button[data-sid]');
    ev.stopPropagation();
    if (!b) return;
    menu.remove();
    let body = { series_id: null };
    if (b.dataset.sid === 'new') {
      const name = (await askText(t('seriesNamePrompt'), m.title) || '').trim();
      if (!name) return;
      body = { new_name: name };
    } else if (b.dataset.sid !== 'none') body = { series_id: Number(b.dataset.sid) };
    const res = await fetch(`/api/meetings/${m.id}/series`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) return;
    const data = await readJson(res);
    if (state.activeMeeting && state.activeMeeting.id === m.id) { state.activeMeeting.series_id = data.series_id; renderSeriesUi(); }
    await loadMeetingsList();
    loadSeries();
  });
  document.body.appendChild(menu);
  menu.style.left = `${Math.min(e.clientX, window.innerWidth - 250)}px`;
  menu.style.top = `${Math.min(e.clientY, window.innerHeight - 330)}px`;
}

document.addEventListener('click', () => document.getElementById('series-menu')?.remove());

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

// One click (the chips in the transcript toolbar): shows the translation, or makes it
async function quickTranslate(lang) {
  hideTranslateMenu();
  const m = state.activeMeeting;
  if (!m) return;
  if (meetingTranslations(m)[lang]) {
    state.txLang = lang;
    setTranscriptView(state.txView === 'orig' || !state.txView ? 'both' : state.txView);
    return;
  }
  await startTranslation(false, lang);
}

function hideTranslateMenu() {
  document.getElementById('translate-menu')?.classList.add('hidden');
}

async function ctxRenameRecording() {
  hideContextMenu();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  if (!m) return;
  const newTitle = await askText(state.uiLang === 'en' ? 'New recording title:' : 'Новое название записи:', m.title);
  if (!newTitle || !newTitle.trim() || newTitle.trim() === m.title) return;

  const res = await fetch(`/api/meetings/${m.id}/title`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: newTitle.trim() })
  });
  if (!res.ok) { showToast((await readJson(res)).detail || 'Error'); return; }
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
  const modelSel = document.getElementById('reconfig-model');
  modelSel.value = m.model_name || 'large-v3-turbo';
  if (!modelSel.value && modelSel.options.length) modelSel.selectedIndex = 0;  // a removed model
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

// Delete confirmation for one or several recordings: what is always deleted, and the files on the
// disk that can go too (the source used in place, saved exports, the Obsidian note)
function confirmDeleteDialog(list) {
  if (!Array.isArray(list)) list = [list];
  return new Promise(resolve => {
    const sources = list.filter(m => m.source === 'local');
    const exportsN = list.reduce((n, m) => n + (m.exports || []).length, 0);
    const notes = list.filter(m => m.obsidian_path);
    const opt = (id, label, detail) => `<label class="flex items-start gap-2.5 cursor-pointer text-xs text-apple-label">
        <input type="checkbox" id="${id}" class="del-opt mt-0.5 rounded bg-apple-sidebar border-white/20 text-apple-red" />
        <span>${escapeHtml(label)}${detail ? `<span class="block text-[11px] text-apple-tertiary mt-0.5 break-all line-clamp-2">${escapeHtml(detail)}</span>` : ''}</span></label>`;
    const options = [
      sources.length ? opt('del-with-source', t('deleteWithSource'), sources.map(m => m.filename).join(', ')) : '',
      exportsN ? opt('del-with-exports', t('deleteWithExports').replace('{n}', () => exportsN), list.flatMap(m => m.exports || []).slice(0, 3).join(', ')) : '',
      notes.length ? opt('del-with-note', t('deleteWithNote').replace('{n}', () => notes.length), notes.map(m => m.obsidian_path).slice(0, 2).join(', ')) : '',
    ].filter(Boolean);
    const what = list.length === 1 ? t('confirmDeleteOne').replace('{title}', () => list[0].title) : t('confirmDeleteMany').replace('{n}', () => list.length);
    const wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 modal-backdrop z-[70] flex items-center justify-center p-4';
    wrap.innerHTML = `<div class="w-full max-w-md bg-apple-card border apple-border rounded-2xl shadow-2xl p-5 space-y-4 modal-card">
      <div class="text-sm font-semibold text-white">${escapeHtml(t('deleteTitle'))}</div>
      <p class="text-xs text-apple-secondary leading-relaxed">${escapeHtml(what)}</p>
      <p class="text-[11px] text-apple-tertiary leading-relaxed">${escapeHtml(t('deleteAlways'))}</p>
      ${options.length ? `<div class="space-y-2.5"><div class="text-[11px] text-apple-secondary">${escapeHtml(t('deleteAlsoFiles'))}</div>${options.join('')}</div>` : ''}
      <div class="flex justify-end gap-2">
        ${options.length > 1 ? `<button data-a="all" class="ui-btn ui-btn-subtle mr-auto">${escapeHtml(t('deleteSelectAll'))}</button>` : ''}
        <button data-a="no" class="ui-btn ui-btn-subtle">${escapeHtml(t('cancel'))}</button>
        <button data-a="yes" class="ui-btn bg-apple-red text-white hover:opacity-90">${escapeHtml(t('ctxDelete'))}</button>
      </div></div>`;
    const done = (v) => { wrap.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
    const checked = id => !!wrap.querySelector('#' + id)?.checked;
    wrap.addEventListener('click', (e) => {
      const a = e.target.closest('button[data-a]');
      if (e.target === wrap || (a && a.dataset.a === 'no')) done(null);
      else if (a && a.dataset.a === 'all') wrap.querySelectorAll('.del-opt').forEach(c => { c.checked = true; });
      else if (a) done({ withSource: checked('del-with-source'), withExports: checked('del-with-exports'), withNote: checked('del-with-note') });
    });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(wrap);
    wrap.querySelector('button[data-a="no"]').focus();  // Enter after Delete must not remove recordings
  });
}

// The query of DELETE /api/meetings/{id} for the options chosen in the dialog
function deleteQuery(choice, m) {
  const q = new URLSearchParams();
  if (choice.withSource && m.source === 'local') q.set('with_source', 'true');
  if (choice.withExports) q.set('with_exports', 'true');
  if (choice.withNote) q.set('with_note', 'true');
  const s = q.toString();
  return s ? `?${s}` : '';
}

async function ctxDeleteRecording() {
  hideContextMenu();
  const m = state.meetings.find(x => x.id === state.contextMeetingId);
  if (!m) return;
  const choice = await confirmDeleteDialog(m);
  if (!choice) return;

  const delRes = await fetch(`/api/meetings/${m.id}${deleteQuery(choice, m)}`, { method: 'DELETE' });
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
