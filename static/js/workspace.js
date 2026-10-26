// VerbaScribe page script, part 5 of 9: The open recording: audio tracks, dubbing, translation, the transcript.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// --- Active Meeting Workspace ---
let _selectSeq = 0;

async function selectMeeting(id, reloadPlayer = true) {
  // Only the last request wins: a slow answer for an earlier click must not replace the open recording
  const seq = ++_selectSeq;
  // Audit 3.2. The click answers at once: the row lights up and the title shows the recording,
  // the rest comes with the server's answer
  const known = (state.meetings || []).find(x => x.id === id);
  if (known && (!state.activeMeeting || state.activeMeeting.id !== id)) {
    state.pendingMeetingId = id;
    renderMeetingsSidebar();
    document.getElementById('meeting-title-input').value = known.title || '';
  }
  const res = await fetch(`/api/meetings/${id}`).catch(() => null);
  if (seq !== _selectSeq) return;
  if (state.pendingMeetingId === id) {
    state.pendingMeetingId = null;
    if (!res || !res.ok) {
      renderMeetingsSidebar();
      if (state.activeMeeting) document.getElementById('meeting-title-input').value = state.activeMeeting.title || '';
    }
  }
  if (!res || !res.ok) return;
  const m = await readJson(res);
  const prevId = state.activeMeeting ? state.activeMeeting.id : null;
  state.activeMeeting = m;
  if (prevId !== m.id) state.speakerFilter = null;

  document.getElementById('workspace-empty').classList.add('hidden');
  document.getElementById('workspace-content').classList.remove('hidden');
  renderTopbar();

  document.getElementById('meeting-title-input').value = m.title;
  document.getElementById('meeting-meta-badge').textContent =
    `${formatTime(m.duration)} · ${(m.language || 'ru').toUpperCase()} · ${formatModelName(m.transcript_model || m.model_name)}${m.media_removed ? ' · ' + t('mediaRemovedBadge') : ''}`;
  document.getElementById('meeting-meta-badge').title = document.getElementById('meeting-meta-badge').textContent;

  updateProcessingBanner(m);
  renderTxModelBar();
  renderAudioTrackSwitch();
  renderFillerButton();
  renderSeriesUi();
  if (prevId !== m.id || state.activeWorkspaceTab === 'summary') renderAnalysisEstimate();

  if (reloadPlayer || prevId !== m.id) {
    const player = document.getElementById('media-player');
    state.audioTrack = 'orig';
    // The sound may have been deleted for privacy: the transcript and the analysis stay
    if (m.media_removed) player.removeAttribute('src'); else player.src = `/api/meetings/${m.id}/media`;
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
  rpRender();  // the speaker under the player and the colours of the wave follow the text (deleted, a new model)
  renderSummaryPanel();
  renderMeetingsSidebar();
  if (state.activeWorkspaceTab === 'info' && (prevId !== m.id || reloadPlayer)) loadMeetingInfo();
  if (state.appView === 'ask') renderAskScope();
}

// --- The model of the transcript (the row above the transcript tools). Each model's transcript is
// kept (app/database.py transcript_versions): picking a model that made one shows it, another one
// offers Transcribe; the status says which model the text below comes from ---
function txModelChoices(m) {
  const done = new Set([m.transcript_model, ...(m.transcript_versions || []).map(v => v.model)].filter(Boolean));
  const list = (state.whisperCatalog || []).map(i => ({ id: i.id, installed: i.installed, size: modelSizeLabel(i), languages: i.languages }));
  for (const id of done) if (!list.some(i => i.id === id)) list.push({ id, installed: true, size: '' });  // a removed model
  return { list, done };
}

function toggleTxModelBar(open) {
  state.txBarOpen = typeof open === 'boolean' ? open : !state.txBarOpen;
  renderTxModelBar();
}

function renderTxModelBar() {
  const m = state.activeMeeting;
  const bar = document.getElementById('tx-model-bar');
  if (!bar) return;
  const chip = document.getElementById('tx-model-chip');
  if (!m) { bar.classList.add('hidden'); chip.classList.add('hidden'); return; }
  if (state.txModelFor !== m.id) { state.txModelFor = m.id; state.txModelPick = null; state.txBarOpen = false; }
  const { list, done } = txModelChoices(m);
  const shown = (m.segments || []).length ? (m.transcript_model || '') : '';
  // Audit 8.4: when a text is shown and nothing is running, the row folds into the model's mark
  const folded = !!shown && DONE_STATUSES.includes(m.status) && !state.txBarOpen;
  bar.classList.toggle('hidden', folded);
  chip.classList.toggle('hidden', !shown || !DONE_STATUSES.includes(m.status));
  chip.setAttribute('aria-expanded', String(!folded));
  chip.innerHTML = `<span class="tx-model-chip-dot" aria-hidden="true"></span><span>${escapeHtml(formatModelName(shown))}</span><span aria-hidden="true">${folded ? '▾' : '▴'}</span>`;
  const pick = state.txModelPick || shown || m.model_name || (list[0] && list[0].id) || '';
  const lang = String(m.language || '').slice(0, 2);
  const sel = document.getElementById('tx-model-select');
  sel.innerHTML = list.map(i => {
    // GigaAM knows Russian only: not offered for a recording in another language
    const fits = !i.languages || !lang || i.languages.includes(lang) || done.has(i.id);
    return `<option value="${escapeHtml(i.id)}" ${fits ? '' : 'disabled'}>${escapeHtml(formatModelName(i.id))}${done.has(i.id) ? '  ✓' : ''}${
      i.installed ? '' : `  ↓ ${escapeHtml(i.size)}`}${fits ? '' : ` (${escapeHtml(t('txModelOtherLang'))})`}</option>`;
  }).join('');
  sel.value = pick;
  const busy = !DONE_STATUSES.includes(m.status);
  sel.disabled = busy;
  document.getElementById('tx-proc-btn').classList.toggle('hidden', pick === 'merged');  // not made by a model
  if (busy) hideTxProcMenu();
  const btn = document.getElementById('tx-transcribe-btn');
  btn.disabled = busy || !!m.media_removed;  // without the sound there is nothing to transcribe
  btn.classList.toggle('opacity-40', btn.disabled);
  btn.classList.toggle('hidden', pick === 'merged');  // the merged text is not made by a model
  const again = !!pick && pick === shown;
  document.getElementById('tx-transcribe-label').textContent = t(again ? 'txTranscribeAgain' : 'txTranscribe');
  // The next step while the picked model has no text yet: the call to action stands out in blue
  btn.classList.toggle('ui-btn-primary', !again);
  btn.classList.toggle('ui-btn-subtle', again);
  // Merging: once two models have transcribed the recording
  const mergeBtn = document.getElementById('tx-merge-btn');
  mergeBtn.classList.toggle('hidden', [...done].filter(x => x !== 'merged').length < 2);
  mergeBtn.disabled = busy;
  mergeBtn.classList.toggle('opacity-40', busy);
  // Clearing: the transcript of the model picked, when it made one (the one shown)
  const delBtn = document.getElementById('tx-delete-version-btn');
  delBtn.classList.toggle('hidden', !pick || pick !== shown);
  delBtn.disabled = busy;
  delBtn.classList.toggle('opacity-40', busy);
  const disputes = (m.segments || []).reduce((n, s) => n + (s.words || []).filter(w => w.alts && w.alts.length).length, 0);
  const status = document.getElementById('tx-model-status');
  let text, cls;
  if (busy) [text, cls] = [m.status === 'merging' ? t('txMergingBusy') : t('txModelBusy').replace('{model}', () => formatModelName(m.model_name)), 'text-apple-secondary'];
  else if (pick === 'merged' && shown === 'merged') [text, cls] = [`✓ ${t('txMergedDone')}${m.transcript_at ? ' · ' + formatDateTime(m.transcript_at) : ''}${
    disputes ? ' · ' + t('txDisputes').replace('{n}', () => disputes) : ''}`, 'text-apple-green'];
  else if (pick && pick === shown) [text, cls] = [`✓ ${t('txModelDone')}${m.transcript_at ? ' · ' + formatDateTime(m.transcript_at) : ''}`, 'text-apple-green'];
  else if (!shown) [text, cls] = [t('txModelNone'), 'text-apple-secondary'];
  else [text, cls] = [t('txModelNotYet').replace('{model}', () => formatModelName(shown)), 'text-apple-amber'];
  status.textContent = text;
  status.title = text;
  status.className = `tx-model-status ${cls}`;  // goes to a line of its own when the panel is narrow
}

async function pickTxModel(sel) {
  const m = state.activeMeeting;
  if (!m) return;
  const id = sel.value;
  state.txModelPick = id;
  if (id !== m.transcript_model && (m.transcript_versions || []).some(v => v.model === id)) {
    const res = await fetch(`/api/meetings/${m.id}/transcript-version`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: id }),
    });
    const data = await readJson(res);
    state.txModelPick = null;
    if (!res.ok) { showToast(errorText(data, res)); renderTxModelBar(); return; }
    await selectMeeting(m.id, false);
    loadMeetingsList();
    return;
  }
  renderTxModelBar();
}

function disputeTitle(w) {
  return [t('tipDisputed'), ...w.alts.map(a => `${a.text ? '«' + a.text + '»' : t('altNothing')} — ${a.models.map(formatModelName).join(', ')}`)].join('\n');
}

function openWordAlts(e, segId, wi) {
  e.stopPropagation();  // the click would close the menu it opens
  const seg = ((state.activeMeeting && state.activeMeeting.segments) || []).find(s => s.id === segId);
  const w = seg && (seg.words || [])[wi];
  if (!w || !w.alts) return;
  const shown = seg.words.slice(wi, wi + Number(w.span || 1)).map(x => String(x.word).trim()).join(' ');
  const items = w.alts.map(a => ({
    label: `${a.text ? '«' + a.text + '»' : t('altNothing')}${a.text === shown ? '  ✓' : ''}`,
    key: a.models.map(formatModelName).join(', '),
    run: () => chooseWordAlt(segId, wi, a.text),
  }));
  items.push({ sep: true }, { label: t('ctxPlayFromHere'), key: formatTime(Number(w.start)), run: () => seekPlayer(Number(w.start)) });
  const r = e.target.getBoundingClientRect();
  openTextMenuAt(items, r.left, r.bottom + 4);
}

async function chooseWordAlt(segId, wi, text) {
  const m = state.activeMeeting;
  if (!m) return;
  const res = await fetch(`/api/meetings/${m.id}/segments/${segId}/alternative`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ word: wi, text }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }
  if (state.activeMeeting !== m) return;
  m.segments = data.segments;
  renderTranscript();
  renderTxModelBar();
}

async function deletePickedTranscript() {
  const m = state.activeMeeting;
  if (!m) return;
  const model = document.getElementById('tx-model-select').value;
  const others = (m.transcript_versions || []).filter(v => v.model !== model).length;
  if (!(await confirmDialog(t('txDeleteVersionTitle').replace('{model}', () => formatModelName(model)),
    t(others ? 'txDeleteVersionText' : 'txDeleteVersionLast'), t('txDeleteVersion')))) return;
  const res = await fetch(`/api/meetings/${m.id}/transcript-versions/${encodeURIComponent(model)}`, { method: 'DELETE' });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }
  state.txModelPick = null;
  await loadMeetingsList();
  await selectMeeting(m.id, false);
  showToast(t('txVersionDeleted').replace('{model}', () => formatModelName(model)));
}

async function mergeTranscripts() {
  const m = state.activeMeeting;
  if (!m) return;
  const hasMerged = m.transcript_model === 'merged' || (m.transcript_versions || []).some(v => v.model === 'merged');
  if (hasMerged && !(await confirmDialog(t('txMergeAgainTitle'), t('txMergeAgainText'), t('txMerge'), false))) return;
  const res = await fetch(`/api/meetings/${m.id}/merge-transcripts`, { method: 'POST' });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }
  state.txModelPick = null;
  await loadMeetingsList();
  await selectMeeting(m.id, false);
  showToast(t('txMergeStarted'));
}

// --- What is done to the sound before recognition and to the text after it, for this recording
// ("Parameters" next to the model). Starts from what the recording used last (database.effective_processing:
// chosen for it, else the settings) and goes with the next Transcribe
const TX_PROC_GROUPS = [
  ['txProcSound', [['denoise', 'txProcDenoise', 'txProcDenoiseHint'], ['loudnorm', 'txProcLoudnorm', 'txProcLoudnormHint'],
    ['leveling', 'txProcLeveling', 'txProcLevelingHint'], ['vad', 'txProcVad', 'txProcVadHint']]],
  ['txProcText', [['diarization', 'txProcDiarization', 'txProcDiarizationHint']]],
];

function txProc(m) {
  if (!state.txProc || state.txProc.id !== m.id) {
    state.txProc = { id: m.id, values: { ...(m.processing || {}) }, language: m.language || 'auto', speakers: Number(m.num_speakers_hint || 0) };
  }
  return state.txProc;
}

function renderTxProcMenu() {
  const m = state.activeMeeting;
  const box = document.getElementById('tx-proc-menu');
  if (!m || !box) return;
  const p = txProc(m);
  const check = ([key, label, hint]) => `<label class="flex items-start gap-2 cursor-pointer">
      <input type="checkbox" ${p.values[key] !== false ? 'checked' : ''} data-change="setTxProc" data-change-args='[${jsArg(key)}, {"$": "checked"}]'
        class="mt-0.5 rounded bg-apple-sidebar border-white/20 text-apple-blue" />
      <span class="min-w-0"><span class="block text-xs text-apple-label">${escapeHtml(t(label))}</span>
        <span class="block text-[11px] text-apple-tertiary leading-snug">${escapeHtml(t(hint))}</span></span></label>`;
  const select = (key, source, value) => `<select data-change="setTxProcValue" data-change-args='[${jsArg(key)}, {"$": "value"}]'
      class="mt-1 w-full h-7 bg-apple-sidebar border apple-border rounded-[7px] px-2 text-xs text-white focus:outline-none">${document.getElementById(source).innerHTML}</select>`;
  box.innerHTML = TX_PROC_GROUPS.map(([title, items]) => `<div class="space-y-1.5">
      <div class="text-[11px] font-semibold uppercase tracking-wider text-apple-tertiary">${escapeHtml(t(title))}</div>${items.map(check).join('')}</div>`).join('')
    + `<div class="grid grid-cols-2 gap-2">
        <label class="block text-[11px] text-apple-tertiary">${escapeHtml(t('speechLangLabel'))}${select('language', 'reconfig-language', p.language)}</label>
        <label class="block text-[11px] text-apple-tertiary">${escapeHtml(t('speakersCountLabel'))}${select('speakers', 'reconfig-speakers', p.speakers)}</label></div>
      <p class="text-[11px] text-apple-tertiary">${escapeHtml(t('txProcApplies'))}</p>`;
  const [lang, spk] = box.querySelectorAll('select');
  lang.value = p.language;
  if (lang.value !== p.language) lang.value = 'auto';
  spk.value = String(p.speakers);
}

function toggleTxProcMenu(e) {
  e.stopPropagation();
  const box = document.getElementById('tx-proc-menu');
  const open = box.classList.contains('hidden');
  if (open) renderTxProcMenu();
  box.classList.toggle('hidden', !open);
}

function hideTxProcMenu() {
  document.getElementById('tx-proc-menu')?.classList.add('hidden');
}
document.addEventListener('click', hideTxProcMenu);

function setTxProc(key, checked) {
  if (state.activeMeeting) txProc(state.activeMeeting).values[key] = !!checked;
}

function setTxProcValue(key, value) {
  if (!state.activeMeeting) return;
  const p = txProc(state.activeMeeting);
  if (key === 'speakers') p.speakers = Number(value) || 0; else p.language = value;
}

async function transcribeWithPickedModel() {
  const m = state.activeMeeting;
  if (!m) return;
  const model = document.getElementById('tx-model-select').value;
  if (!model) return;
  if (model === m.transcript_model && (m.segments || []).length
      && !(await confirmDialog(t('txTranscribeAgainTitle'), t('txTranscribeAgainText').replace('{model}', () => formatModelName(model)),
        t('txTranscribeAgain'), false))) return;
  hideTxProcMenu();
  const p = txProc(m);
  const res = await fetch(`/api/meetings/${m.id}/reconfigure`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_name: model, language: p.language || 'auto', num_speakers_hint: p.speakers, processing: p.values }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }
  state.txModelPick = null;
  await loadMeetingsList();
  await selectMeeting(m.id, false);
  showToast(t('txTranscribeStarted').replace('{model}', () => formatModelName(model)));
}

function updateProcessingBanner(m) {
  const isBusy = !DONE_STATUSES.includes(m.status);
  const banner = document.getElementById('workspace-processing-banner');
  banner.classList.toggle('hidden', !isBusy && !['failed', 'cancelled', 'cleared'].includes(m.status));
  document.getElementById('workspace-cancel-btn').classList.toggle('hidden', !isBusy);
  document.getElementById('workspace-retry-btn').classList.toggle('hidden', isBusy);
  const bar = document.getElementById('workspace-progress-bar');
  const q = (state.queue || []).find(x => x.id === m.id);
  const running = isBusy && q && q.state === 'running';
  bar.classList.toggle('progress-live', !!running);
  document.getElementById('workspace-live-dot').classList.toggle('hidden', !running);
  document.getElementById('workspace-progress-time').textContent = running ? formatTiming(jobTiming(q, m.progress || 0)) : '';
  if (isBusy) {
    document.getElementById('workspace-stage-text').classList.add('truncate');
    document.getElementById('workspace-stage-text').textContent = serverText(m.stage_text) || t('processingShort');
    document.getElementById('workspace-progress-pct').textContent = `${m.progress}%`;
    bar.style.background = '';
    bar.style.width = `${Math.max(running ? 3 : 0, m.progress || 0)}%`;
  } else {
    // Failed or cancelled: the reason, a red (or grey) bar and Retry, not a "finished" blue bar
    const failed = m.status === 'failed';
    // the reason in words; the server's technical text (an exception, a path) only behind «Подробнее»
    const err = humanError(m.error_message || '');
    document.getElementById('workspace-stage-text').textContent = m.status === 'cleared' ? t(Object.keys(m.summaries || {}).length ? 'statusNoText' : 'statusCleared')
      : (failed ? t('statusFailed') : t('statusCancelled')) + (m.error_message ? `: ${err.short}` : m.stage_text ? `: ${serverText(m.stage_text)}` : '');
    document.getElementById('workspace-stage-text').title = m.error_message ? err.short : '';
    document.getElementById('workspace-stage-text').classList.toggle('truncate', !failed);  // a reason is read in full
    document.getElementById('workspace-error-more').classList.toggle('hidden', !(failed && err.tech));
    document.getElementById('workspace-progress-pct').textContent = '';
    bar.style.width = '100%';
    bar.style.background = failed ? 'rgba(255, 69, 58, 0.7)' : 'rgba(255, 255, 255, 0.2)';
  }
}

function showProcessingErrorDetails() {
  const m = state.activeMeeting;
  if (!m || !m.error_message) return;
  const err = humanError(m.error_message);
  confirmDialog(t('errDetailsTitle'), `${err.short}\n\n${err.tech}`, t('copy'), false, { cancel: t('close') })
    .then(ok => { if (ok) copyPlain(err.tech); });
}

async function retryActiveMeeting() {
  const m = state.activeMeeting;
  if (!m) return;
  const res = await fetch(`/api/meetings/${m.id}/retry`, { method: 'POST' });
  if (!res.ok) { showError((await readJson(res)).detail); return; }
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
      <select data-change="setAudioTrack" data-change-args='[{"$": "value"}]' class="bg-transparent focus:outline-none cursor-pointer text-[11.5px]">
        ${option('orig', t('trackOriginal'))}
        ${dubs.map(d => option(d.lang, `${t('trackDub')} ${d.lang.toUpperCase()}`)).join('')}
      </select>
    </label>${onDub ? `
    <button data-click="deleteActiveDub" class="ui-btn ui-btn-subtle px-2" title="${escapeHtml(t('dubDelete'))}">✕</button>` : ''}`;
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
  showToast(key === 'orig' ? t('trackOriginalOn') : t('trackDubOn').replace('{lang}', () => dubLangName(key)));
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
    showError(t('exportFailed'));
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

  const meta = st.outdated && !st.active ? [t('dubSetupOutdated')] : [];
  meta.push(t('dubSetupSize').replace('{gb}', () => st.download_gb ?? 6));
  if (st.free_gb != null) meta.push(t('dubSetupFree').replace('{gb}', () => st.free_gb));
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
  btn.className = `ui-btn ${env.installed ? 'ui-btn-subtle' : 'ui-btn-primary'} h-7 px-3 text-[11.5px]`;
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
    .replace('{gb}', () => st.download_gb ?? 6)
    .replace('{dir}', () => st.venv || '.venv-dub');
  if (!await askConfirm(question, false, t(state.dubEnvInstalled ? 'cfReinstall' : 'cfDownloadInstall'))) return;
  let res = null;
  try { res = await fetch('/api/dub/setup', { method: 'POST' }); } catch (e) { console.error(e); }
  const setup = res ? await res.json().catch(() => ({})) : {};
  if (!res || !res.ok) {
    // e.g. an older server process is still running behind this window
    const code = res ? res.status : 0;
    const failed = { installed: false, setup: { ...st, active: false, error: t('dubSetupRequestFailed').replace('{code}', () => code || '—') } };
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
        if (env.installed) showToast(t('dubSetupDone'), null, 'success');
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
// File sizes in the units Windows Explorer uses (1 KB = 1024 bytes), so the numbers match it
function formatBytes(n) {
  if (n == null) return '…';
  if (n >= 2 ** 30) return `${(n / 2 ** 30).toFixed(1)} ${t('unitGB')}`;
  if (n >= 10 * 2 ** 20) return `${Math.round(n / 2 ** 20)} ${t('unitMB')}`;
  if (n >= 2 ** 20) return `${(n / 2 ** 20).toFixed(1)} ${t('unitMB')}`;
  return `${Math.round(n / 1024)} ${t('unitKB')}`;
}

// One weight file of the dub models, from its row
function downloadDubWeight(id) {
  downloadDubWeights([id]);
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
  pill.className = `text-[11px] font-mono px-2.5 py-0.5 rounded-full shrink-0 ${pillCls}`;

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
        <button data-id="${escapeHtml(item.id)}" data-click="deleteDubWeights" data-click-args='[{"$": "data", "k": "id"}]' ${busy ? 'disabled style="opacity:.4"' : ''}
          title="${escapeHtml(t('tipDeleteModel'))}" class="ui-btn ui-btn-subtle px-2 hover:text-apple-red">${SVG_ICONS.trash}</button>`;
    } else {
      action = `<button data-id="${escapeHtml(item.id)}" data-click="downloadDubWeight" data-click-args='[{"$": "data", "k": "id"}]' ${busy || !info.installed ? 'disabled style="opacity:.4"' : ''}
          title="${escapeHtml(info.installed ? t('tipDownloadModel') : t('sdubNeedEnv'))}" class="ui-btn ui-btn-primary">${SVG_ICONS.download}<span>${escapeHtml(t('downloadBtn'))} (${escapeHtml(formatBytes(item.expected_bytes))})</span></button>`;
    }
    const size = item.size_bytes ? formatBytes(item.size_bytes) : t('sdubNotDownloaded');
    return `<div class="p-3 rounded-xl border bg-apple-sidebar border-white/10 flex items-center justify-between gap-3">
      <div class="min-w-0">
        <div class="flex items-center flex-wrap gap-2">
          <span class="text-xs font-semibold text-white">${escapeHtml(item.name)}</span>
          <span class="text-[11px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-apple-secondary">${escapeHtml(item.license)}</span>
          <span class="text-[11px] text-apple-tertiary">${escapeHtml(size)}</span>
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
    .replace('{env}', () => formatBytes(d.env_bytes))
    .replace('{weights}', () => formatBytes(d.weights_bytes))
    .replace('{outputs}', () => formatBytes(d.outputs_bytes))
    .replace('{free}', () => formatBytes(d.free_bytes));

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
    .replace('{names}', () => items.map(i => i.name).join(', '))
    .replace('{size}', () => formatBytes(total))
    .replace('{dir}', () => state.dubInfo.weights.dir);
  // Audit 6.6: the size is on the button, so it just downloads; it asks only when the disk is short
  const disk = await fetch('/api/disk').then(r => r.json()).catch(() => null);
  const short = !disk || disk.free_bytes < 0 || disk.free_bytes < total * 1.2;
  if (short && !await askConfirm(`${question}\n\n${t('sdubDiskShort').replace('{free}', () => formatBytes(Math.max(0, (disk && disk.free_bytes) || 0)))}`, false, t('cfDownload'))) return;
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
  if (!item || !await askConfirm(t('sdubDeleteConfirm').replace('{name}', () => item.name).replace('{size}', () => formatBytes(item.size_bytes)), true, t('cfDeleteWeights'))) return;
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
    showError(res.ok ? (data.ok ? t('sdubVerifyOk') : t('sdubVerifyFailed')) : (data.detail));
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
    box.innerHTML = `<div class="text-[11.5px] text-apple-tertiary">${escapeHtml(t('dubNone'))}</div>`;
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
          <div class="text-[11px] text-apple-secondary truncate" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
          <div class="text-[11px] text-apple-tertiary">${escapeHtml(meta)}</div>
        </div>
        <button data-click="revealDub" data-click-args='[${jsArg(lang)}]' class="ui-btn ui-btn-subtle h-6 px-2 text-[11.5px] shrink-0">${escapeHtml(t('dubReveal'))}</button>
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
    showError(data.detail);
    return;
  }
  closeModal('dub');
  const pending = state.pendingDubExport;
  state.pendingDubExport = null;
  if (pending) {
    // The export that asked for this dub is saved when the dub is ready, in the language the window
    // ended with (it may have been changed there)
    try {
      const r = await fetch(`/api/meetings/${m.id}/export-request`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...pending, lang: document.getElementById('dub-lang').value }),
      });
      const d = await r.json().catch(() => ({}));
      showToast(r.ok && d.status === 'queued' ? t('exportQueued').replace('{what}', () => t('exportWaiting_dub')).replace('{dir}', () => d.folder || '') : t('dubQueued'));
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
    showError(data.detail);
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
  // Without ICU data for a language the browser returns the code itself: then the dub list's own names
  const own = () => ((I18N[state.uiLang] || I18N.ru).dubLangNames || {})[code] || String(code).toUpperCase();
  try {
    const n = new Intl.DisplayNames([state.uiLang], { type: 'language' }).of(code);
    return n && n.toLowerCase() !== String(code).toLowerCase() ? n.charAt(0).toUpperCase() + n.slice(1) : own();
  } catch (e) {
    return own();
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
    box.innerHTML = `<div class="text-[11.5px] text-apple-tertiary">${escapeHtml(t('translateNone'))}</div>`;
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
          <div class="text-[11px] text-apple-tertiary truncate">${escapeHtml(meta)}</div>
          ${outdated ? `<div class="text-[11px] text-apple-amber">${escapeHtml(t('translateOutdated').replace('{stale}', () => r.stale || 0).replace('{missing}', () => r.missing || 0))}</div>` : ''}
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button data-click="showTranslation" data-click-args='[${jsArg(lang)}]' class="ui-btn ui-btn-subtle h-6 px-2 text-[11.5px]">${escapeHtml(t('translateShow'))}</button>
          ${outdated ? `<button data-click="startTranslation" data-click-args='[true, ${jsArg(lang)}]' class="ui-btn ui-btn-primary h-6 px-2 text-[11.5px]" title="${escapeHtml(t('tipTranslateUpdate'))}">${escapeHtml(t('translateUpdate'))}</button>` : ''}
          <button data-click="startTranslation" data-click-args='[false, ${jsArg(lang)}]' class="ui-btn ui-btn-subtle h-6 px-2 text-[11.5px]" title="${escapeHtml(t('tipTranslateRedo'))}">${escapeHtml(t('translateRedo'))}</button>
          <button data-click="deleteTranslation" data-click-args='[${jsArg(lang)}]' class="ui-btn ui-btn-subtle h-6 px-1.5 text-[11.5px] hover:text-apple-red" title="${escapeHtml(t('tipTranslateDelete'))}">${SVG_ICONS.trash}</button>
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
  sel.innerHTML = langs.map(c => `<option value="${c}">${escapeHtml(t('subsLangTranslation').replace('{lang}', () => langName(c)))}</option>`).join('')
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
    showError(data.detail);
    return;
  }
  state.txLang = target;
  closeModal('translate');
  showToast(t('translateQueued'));
  await loadMeetingsList();
}

// Audit 6.1. A translation goes at once and «Отменить» brings it back for 7 s; the server forgets it
// only after that (or when the window closes)
function removeTranslationWithUndo(lang) {
  const m = state.activeMeeting;
  if (!m || !meetingTranslations(m)[lang]) return;
  const kept = meetingTranslations(m)[lang];
  const wasView = state.txView, wasLang = state.txLang;
  const redraw = () => {
    if (!state.activeMeeting || state.activeMeeting.id !== m.id) return;
    renderTranslateResults();
    renderSubsControls();
    renderTranslationControls();
    renderTranscript();
  };
  deleteWithUndo({
    message: t('translationDeletedToast').replace('{lang}', () => langName(lang)),
    hide: () => {
      const rest = { ...meetingTranslations(m) };
      delete rest[lang];
      m.translations = rest;
      if (state.txLang === lang) state.txView = 'orig';
      redraw();
    },
    restore: () => {
      m.translations = { ...meetingTranslations(m), [lang]: kept };
      state.txView = wasView;
      state.txLang = wasLang;
      redraw();
    },
    commit: async (keepalive) => {
      const res = await fetch(`/api/meetings/${m.id}/translations/${encodeURIComponent(lang)}`, { method: 'DELETE', keepalive });
      if (res.ok && !keepalive && state.activeMeeting && state.activeMeeting.id === m.id) {
        const data = await res.json().catch(() => null);
        if (data) { state.activeMeeting = data; redraw(); }
      }
      return res.ok;
    },
  });
}

function deleteTranslation(lang) {
  removeTranslationWithUndo(lang);
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
  if (mode === 'burn' && !await askConfirm(t('subsBurnConfirm'), false, t('cfBurnSubs'))) return false;
  showToast(mode === 'soft' ? t('subsSoftWorking') : t('subsBurnWorking'));
  const res = await fetch(`/api/meetings/${m.id}/subtitles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lang: lang || null, variant, mode, ask_path: !!askPath }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showError(data.detail);
    return false;
  }
  if (data.cancelled) return false;
  if (data.queued) {
    showToast(t('subsBurnQueued').replace('{name}', () => data.filename));
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
    return `<button ${on ? pageCall('click', 'setTranscriptView', ['orig']) : pageCall('click', 'quickTranslate', [c])}
      title="${escapeHtml(on ? t('subsLangOriginal') : ready ? t('translateQuickShow').replace('{lang}', () => langName(c)) : t('translateQuickMake').replace('{lang}', () => langName(c)))}"
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

  const item = (args, mark, label) =>
    `<button ${pageCall('click', args[0], args.slice(1))} class="macos-menu-item gap-2" style="justify-content:flex-start">`
    + `<span class="w-3 shrink-0 text-center">${mark}</span><span class="truncate">${escapeHtml(label)}</span></button>`;
  const sep = '<div class="h-px bg-white/10 my-1"></div>';
  let html = item(['setTxViewChoice', 'orig'], view === 'orig' ? '✓' : '', src ? `${t('txViewOrig')} (${src})` : t('txViewOrig'));
  for (const c of langs) {
    const on = c === state.txLang;
    html += sep;
    html += item(['setTxViewChoice', 'tr', c], view === 'tr' && on ? '✓' : '', `${t('txViewTr')}: ${langName(c)}`);
    html += item(['setTxViewChoice', 'both', c], view === 'both' && on ? '✓' : '', `${t('txViewBothWith')} ${langName(c)}`);
  }
  if (canTranslate) html += sep + item(['setTxViewChoice', '__new'], '＋', t('txTranslateNew').replace(/^＋\s*/, ''));
  // Clearing: one translation, or everything the processing made
  if (langs.length) html += sep + langs.map(c => item(['deleteTranslationFromMenu', c], '✕', `${t('txDeleteTr')}: ${langName(c)}`)).join('');
  document.getElementById('tx-view-menu').innerHTML = html;
  renderExportTranslatedMenu();
}

// From the "Text language" menu of the transcript (deleteTranslation above is the Translate window's)
function deleteTranslationFromMenu(lang) {
  hideTxViewMenu();
  removeTranslationWithUndo(lang);
}


async function deleteActiveDub() {
  const m = state.activeMeeting;
  const lang = state.audioTrack;
  if (!m || !lang || lang === 'orig') return;
  if (!(await confirmDialog(t('dubDeleteTitle').replace('{lang}', () => lang.toUpperCase()), t('dubDeleteText'), t('dubDelete')))) return;
  const res = await fetch(`/api/meetings/${m.id}/dubs/${encodeURIComponent(lang)}`, { method: 'DELETE' });
  if (!res.ok) { showError((await readJson(res)).detail); return; }
  setAudioTrack('orig');
  await selectMeeting(m.id, true);
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
    showError(data.detail);
    return;
  }
  showToast(t('uiCancelling'));
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
    fitTranscriptToolbar();
    return;
  }

  // Compact: a speaker icon for "everyone" and a numbered circle in each speaker's colour; the
  // name is in the tooltip, right-click plays a sample of the voice
  const allActive = !state.speakerFilter;
  let html = `<button data-click="filterBySpeaker" data-click-args='[null]' title="${escapeHtml(t('tipFilterAllSpeakers'))}"
      class="spk-chip ${allActive ? 'spk-chip-on' : ''}">${ICON_SPEAKERS}</button>`;
  html += keys.map((spkId, i) => {
    const s = speakers[spkId];
    const active = state.speakerFilter === spkId;
    const named = !/^(Участник|Speaker|SPEAKER)[ _]?\d+$/i.test(s.name || '');
    const label = named ? (s.name || '?').trim().slice(0, 1).toUpperCase() : String(i + 1);
    return `<button data-click="filterBySpeaker" data-click-args='[${jsArg(spkId)}]' data-contextmenu="openSpeakerChipMenu" data-contextmenu-args='[{"$": "event"}, ${jsArg(spkId)}]'
      title="${escapeHtml(`${s.name}\n${t('tipFilterSpeaker')} · ${t('tipSpeakerChipVoice')}`)}"
      class="spk-chip ${active ? 'spk-chip-on' : ''}" style="--spk:${s.color}">${escapeHtml(label)}</button>`;
  }).join('');

  container.innerHTML = html;
  fitTranscriptToolbar();
}

// The tools on the right keep only their icons (their titles explain them) when the speaker
// circles would not fit on one line beside them; a ResizeObserver repeats the check when the
// window, the sidebar or a button's label changes
let txToolbarObserver = null;
function fitTranscriptToolbar() {
  const bar = document.querySelector('#wview-transcript .tx-toolbar');
  const pills = document.getElementById('speaker-pills-container');
  if (!bar || !pills) return;
  if (!txToolbarObserver && window.ResizeObserver) {
    txToolbarObserver = new ResizeObserver(() => fitTranscriptToolbar());
    [bar.parentElement, document.getElementById('tx-tools'), pills].forEach(el => el && txToolbarObserver.observe(el));
  }
  bar.classList.remove('tx-compact');
  const crowded = pills.children.length && (pills.scrollWidth > pills.clientWidth + 1 || pills.offsetHeight > 34);
  if (crowded || bar.scrollWidth > bar.clientWidth + 1) bar.classList.add('tx-compact');
}

// The speakers tab (cards, how the talk went): people.js, renderSpeakersPanel and renderTalkAnalytics

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

// Right click on a speaker circle: rename, hear the voice, show only their lines
function openSpeakerChipMenu(e, spkId) {
  e.preventDefault();
  e.stopPropagation();
  const s = ((state.activeMeeting && state.activeMeeting.speakers) || {})[spkId];
  if (!s) return;
  openTextMenuAt([
    { label: t('spkRename'), run: () => renameSpeakerPrompt(spkId) },
    { label: t('listenBtn'), run: () => previewSpeakerVoice(Number(s.sample_start) || 0, Number(s.sample_end) || 5) },
    { label: state.speakerFilter === spkId ? t('spkShowAll') : t('spkShowOnly'), run: () => filterBySpeaker(state.speakerFilter === spkId ? null : spkId) },
  ], e.clientX, e.clientY);
}

// The speaker's name in every line, the speakers tab and exports (one name per speaker of a recording)
async function renameSpeakerPrompt(spkId) {
  const m = state.activeMeeting;
  const s = ((m && m.speakers) || {})[spkId];
  if (!s) return;
  // in place at the first shown line of this speaker, as from the line itself
  const seg = (m.segments || []).find(x => x.speaker === spkId && document.getElementById(`seg-${x.id}`));
  if (seg) {
    document.getElementById(`seg-${seg.id}`).scrollIntoView({ block: 'center' });
    startInlineSpeakerRename(seg.id);
    return;
  }
  const name = await askText(t('spkRenameLabel').replace('{name}', () => s.name || spkId), s.name || '');
  if (name === null || !name.trim() || name.trim() === s.name) return;
  await renameSpeaker(spkId, name);
}

// One rename for every place a speaker is renamed: the new name in this recording, then — when the
// old name still speaks in other recordings — «the same person?»: yes makes the old name a variant
// of the new one in «Все люди» (one row, its recordings and tasks together); the toast undoes it here
async function renameSpeaker(spkId, value) {
  const m = state.activeMeeting;
  const s = ((m && m.speakers) || {})[spkId];
  const name = String(value || '').trim();
  if (!s || !name || name === s.name) return;
  const old = s.name || spkId;
  await updateSpeaker(spkId, name, null);
  const now = state.activeMeeting;  // updateSpeaker puts the saved recording in its place
  if (!now || now.id !== m.id || ((now.speakers || {})[spkId] || {}).name !== name) return;
  const elsewhere = await fetch('/api/people/directory').then(r => r.ok ? r.json() : []).catch(() => [])
    .then(list => list.find(p => p.name.toLowerCase() === old.toLowerCase() && p.recordings > 0));
  if (elsewhere && old.toLowerCase() !== name.toLowerCase()) {
    const same = await confirmDialog(t('spSameTitle').replace('{old}', () => old),
      t('spSameText').replaceAll('{old}', () => old).replace('{name}', () => name).replace('{n}', () => String(elsewhere.recordings)),
      t('spSameYes'), false, { cancel: t('spSameNo') });
    if (same) {
      const res = await fetch('/api/people/merge', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names: [name, old] }) });
      if (!res.ok) showToast(errorText(await readJson(res), res));
      if (typeof spCtx !== 'undefined') spCtx.data = null;
    }
  }
  showToast(t('spRenamed').replace('{name}', () => name), { label: t('spUndo'), onClick: () => updateSpeaker(spkId, old, null) });
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
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); renderSpeakersPanel(); return; }
  state.activeMeeting = data;
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
    const m = state.activeMeeting || {};
    const why = segments.length ? 'emptyTxFiltered'
      // the only transcript deleted with «Очистить»: the analysis made from it stays
      : m.status === 'cleared' ? (Object.keys(m.summaries || {}).length ? 'emptyTxAnalysisKept' : 'emptyTxCleared')
      : ['failed', 'cancelled'].includes(m.status) ? 'emptyTxFailed'
      : DONE_STATUSES.includes(m.status) ? 'emptyTxNoSpeech' : 'emptyTxProcessing';
    container.innerHTML = `<div class="py-20 text-center text-xs text-apple-tertiary">${escapeHtml(t(why))}</div>`;
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
  rpOnTranscript();
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
    `<option value="${escapeHtml(k)}" ${k === currentSpk ? 'selected' : ''} class="bg-apple-card text-white">${escapeHtml(speakers[k].name)}</option>`
  ).join('');

  const spk = speakers[seg.speaker] || { name: seg.speaker || 'Speaker', color: '#0A84FF' };
  const words = seg.words || [];
  const quiet = rpHiddenWords(seg);  // «Без запинок»: the words it hides, or the text it shows
  const hidden = new Set((quiet && quiet.drop) || []);

  let bodyHtml = '';
  const tr = activeSegmentTranslation(seg);
  if (tr && state.txView === 'tr') {
    // Translation has no word timings: the whole segment is highlighted and seeks to its start
    bodyHtml = translationHtml(seg, tr, query, false);
  } else {
    if (words.length > 0 && !query) {
      // the first word shown after hidden ones starts the phrase: a capital letter, as the text has it
      const lead = hidden.has(0) && /^\s*\p{Lu}/u.test(seg.text || '') ? words.findIndex((_, wi) => !hidden.has(wi)) : -1;
      const cap = (wi, word) => (wi === lead ? String(word).replace(/^(\s*)(\p{Ll})/u, (_, sp, c) => sp + c.toUpperCase()) : word);
      bodyHtml = words.map((w, wi) => hidden.has(wi) ? '' : (w.alts && w.alts.length)
        // A place the models heard differently (a merged transcript): a click shows the variants
        ? `<span class="word-token word-disputed cursor-pointer" data-start="${Number(w.start) || 0}" data-end="${Number(w.end) || 0}" title="${escapeHtml(disputeTitle(w))}" data-click="openWordAlts" data-click-args='[{"$": "event"}, ${Number(seg.id)}, ${wi}]'>${escapeHtml(cap(wi, w.word))}</span>`
        : `<span class="word-token cursor-pointer" data-start="${Number(w.start) || 0}" data-end="${Number(w.end) || 0}" data-click="seekPlayer" data-click-args='[${Number(w.start) || 0}]'>${escapeHtml(cap(wi, w.word))}</span>`
      ).join('');
    } else {
      const shown = quiet && !(words.length && !query) && typeof quiet.text === 'string' ? quiet.text : seg.text;
      bodyHtml = `<span class="cursor-pointer" data-click="seekPlayer" data-click-args='[${Number(seg.start) || 0}]'>${highlightSearch(escapeHtml(shown), query)}</span>`;
    }
    if (tr) bodyHtml += translationHtml(seg, tr, query, true);
  }

  const mergeBtnHtml = idx > 0 ? `
    <button data-click="mergeSegmentWithPrevious" data-click-args='[${seg.id}]'
      title="${escapeHtml(t('tipMergeSegment'))}"
      class="seg-merge-btn opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 inline-flex items-center gap-1 text-[11.5px] text-apple-tertiary hover:text-apple-blue transition">
      ${SVG_ICONS.mergeUp}
      <span>${t('mergeBtn')}</span>
    </button>
  ` : '';

  return `
    <div id="seg-${seg.id}" data-start="${seg.start}" data-end="${seg.end}"
      class="segment-row group px-3.5 py-2.5 flex items-baseline gap-4 hover:bg-white/[0.02]">
      <button data-click="seekPlayer" data-click-args='[${Number(seg.start) || 0}]'
        title="${escapeHtml(t('tipSeekTimestamp'))}"
        class="seg-time text-[11.5px] font-mono text-apple-tertiary hover:text-apple-blue shrink-0 w-12 text-right transition">
        ${formatTime(seg.start)}
      </button>

      <div class="flex-1 min-w-0">
        <div class="flex items-center justify-between mb-0.5">
          <div class="flex items-center min-w-0">
          <select data-change="changeSegmentSpeaker" data-change-args='[${seg.id}, {"$": "value"}]'
            title="${escapeHtml(t('tipChangeSegSpeaker'))}"
            style="color: ${spk.color}"
            class="seg-spk bg-transparent text-xs font-semibold focus:outline-none cursor-pointer">
            ${speakerOptionsHtml(seg.speaker)}
          </select>
          ${speakers[seg.speaker] ? `<button data-click="startInlineSpeakerRename" data-click-args='[${Number(seg.id)}]' class="seg-icon-btn"
            title="${escapeHtml(t('spkRenameOption').replace('{name}', () => speakers[seg.speaker].name))}" aria-label="${escapeHtml(t('spkRenameOption').replace('{name}', () => speakers[seg.speaker].name))}">${SVG_ICONS.pencil}</button>` : ''}
          ${seg.lang && state.activeMeeting && seg.lang !== state.activeMeeting.language
            ? `<span class="ml-2 text-[11px] font-mono px-1 rounded bg-white/10 text-apple-secondary" title="${escapeHtml(t('tipSegLang'))}">${escapeHtml(String(seg.lang).toUpperCase())}</span>` : ''}
          </div>
          <div class="flex items-center gap-2.5">
            ${mergeBtnHtml}
            <button data-click="openInlineSegmentEditor" data-click-args='[${seg.id}]'
              title="${escapeHtml(t('tipEditSegment'))}" class="seg-edit-btn">
              ${SVG_ICONS.pencil}<span>${t('editBtn')}</span>
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
    return `<span class="tr-word" data-start="${Number(t0) || 0}" data-click="stopThen" data-click-args='[{"$": "event"}, "seekPlayer", ${Number(t0) || 0}]'>${escapeHtml(w)}</span>`;
  }).join('');
}

function translationHtml(seg, tr, query, secondary) {
  const staleBadge = tr.stale
    ? ` <span class="text-[11px] text-apple-amber align-middle" title="${escapeHtml(t('tipTranslationStale'))}">${escapeHtml(t('translationStale'))}</span>`
    : '';
  // Without a search the words are separate spans, so playback can follow them (see updatePlaybackHighlight)
  const body = !tr.text
    ? `<span class="italic text-apple-tertiary">${escapeHtml(t('translationMissing'))}</span>`
    : query
      ? highlightSearch(escapeHtml(tr.text), query)
      : translationWordsHtml(seg, tr.text);
  const cls = secondary ? 'block mt-1 text-[13px] text-apple-secondary' : '';
  return `<span class="tr-text cursor-pointer ${cls} ${tr.stale ? 'opacity-70' : ''}" data-click="seekPlayer" data-click-args='[${Number(seg.start) || 0}]'>${body}${staleBadge}</span>`;
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
  const m = state.activeMeeting;
  if (!m) return;
  // the two lines as they were, for «Отменить»
  const segs = m.segments || [];
  const i = segs.findIndex(s => s.id === segId);
  const pair = i > 0 ? [JSON.parse(JSON.stringify(segs[i - 1])), JSON.parse(JSON.stringify(segs[i]))] : null;
  const res = await fetch(`/api/meetings/${m.id}/segments/${segId}/merge`, {
    method: 'POST'
  });
  if (!res.ok) { showToast(errorText(await readJson(res), res)); return; }
  state.activeMeeting = await readJson(res);
  renderSpeakerPillsBar();
  renderSpeakersPanel();
  renderTranscript();
  showToast(t('uiSegmentsMerged'), pair ? { label: t('spUndo'), onClick: () => unmergeSegments(m.id, pair) } : undefined);
}

async function unmergeSegments(meetingId, [first, second]) {
  const res = await fetch(`/api/meetings/${meetingId}/segments/${first.id}/unmerge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ first, second }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }
  if (state.activeMeeting && state.activeMeeting.id === meetingId) {
    state.activeMeeting = data;
    renderSpeakerPillsBar();
    renderSpeakersPanel();
    renderTranscript();
  }
}

// The speaker's name of a line, changed in place (as on the Participants page): Enter saves for every
// line of the recording, Esc puts the name back; the toast offers «Отменить»
function startInlineSpeakerRename(segId) {
  const m = state.activeMeeting;
  const seg = m && (m.segments || []).find(s => s.id === segId);
  const spk = seg && (m.speakers || {})[seg.speaker];
  const row = document.getElementById(`seg-${segId}`);
  const sel = row && row.querySelector('select');
  if (!spk || !sel) return;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = spk.name || seg.speaker;
  input.className = 'seg-name-input';
  input.style.color = spk.color || '';
  input.setAttribute('aria-label', t('spkRenameOption').replace('{name}', () => spk.name || seg.speaker));
  sel.replaceWith(input);
  row.querySelector('[data-click="startInlineSpeakerRename"]')?.classList.add('hidden');
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (!save || !name || name === (spk.name || seg.speaker)) { renderTranscript(); return; }
    await renameSpeaker(seg.speaker, name);
  };
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

// Filler words: one button that removes them or brings them back. After a cleanup (made
// automatically after transcription, or by hand) it shows what was cut: "−12 words · −8 s"
// «Без запинок» hides fillers only when the text is shown (redesign_player.js): the button follows
// the open recording
function renderFillerButton() {
  rpRenderFillerButton();
}

function openInlineSegmentEditor(segId) {
  const m = state.activeMeeting;
  if (!m) return;
  const seg = (m.segments || []).find(s => s.id === segId);
  if (!seg) return;

  const textBox = document.getElementById(`seg-text-${segId}`);
  if (!textBox || document.getElementById(`seg-edit-area-${segId}`)) return;
  textBox.innerHTML = `
    <textarea id="seg-edit-area-${segId}" rows="2" data-keydown="segEditKey" data-keydown-args='[{"$": "event"}, ${Number(segId)}]'
      class="w-full bg-apple-sidebar border border-apple-blue rounded-lg p-2 text-[13px] text-white focus:outline-none mt-1">${escapeHtml(seg.text)}</textarea>
    <div class="flex items-center justify-end gap-2 mt-1.5">
      <span class="mr-auto text-[11.5px] text-apple-secondary">${escapeHtml(t('segEditHint'))}</span>
      <button data-click="renderTranscript" title="${escapeHtml(t('tipCancelBtn'))}" class="ui-btn ui-btn-subtle">${t('cancel')}</button>
      <button data-click="saveSegmentEdit" data-click-args='[${segId}]' title="${escapeHtml(t('tipSaveSettingsBtn'))}" class="ui-btn ui-btn-primary">${t('save')}</button>
    </div>
  `;
  const area = document.getElementById(`seg-edit-area-${segId}`);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
}

function segEditKey(e, segId) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveSegmentEdit(segId); }
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); renderTranscript(); }
}

async function saveSegmentEdit(segId) {
  const m = state.activeMeeting;
  const oldText = ((m && m.segments) || []).find(s => s.id === segId)?.text || '';
  const newText = document.getElementById(`seg-edit-area-${segId}`).value.trim();
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/segments/${segId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: newText })
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }  // the edit box stays open with the text
  state.activeMeeting = data;
  renderTranscript();
  if (newText !== oldText.trim()) showToast(t('segSaved'), { label: t('spUndo'), onClick: () => restoreSegmentText(m.id, segId, oldText) });
}

async function restoreSegmentText(meetingId, segId, text) {
  const res = await fetch(`/api/meetings/${meetingId}/segments/${segId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); return; }
  if (state.activeMeeting && state.activeMeeting.id === meetingId) { state.activeMeeting = data; renderTranscript(); }
}

async function changeSegmentSpeaker(segId, newSpeaker) {
  const res = await fetch(`/api/meetings/${state.activeMeeting.id}/segments/${segId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speaker: newSpeaker })
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(errorText(data, res)); renderTranscript(); return; }
  state.activeMeeting = data;
  renderSpeakerPillsBar();
  renderSpeakersPanel();
  renderTranscript();
}
