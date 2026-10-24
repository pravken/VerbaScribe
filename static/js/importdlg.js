// VerbaScribe page script: the import window (package B, docs/redesign-plan.md). Files, links and paths
// to add; the model, language and speakers; what is done to the sound before recognition and to the
// text after it (database.PROCESSING_KEYS, kept per recording). Filled from the settings; "Remember"
// writes them back, "Do not ask" makes "Import" start at once (setting ask_import_params).
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

const IMP_SOUND = [
  // key, label, hint, the models it is for: 'onnx' (GigaAM, Parakeet), 'whisper', or both
  ['denoise', 'impDenoise', 'impDenoiseHint', ''],
  ['loudnorm', 'impLoudnorm', 'impLoudnormHint', ''],
  ['leveling', 'impLeveling', 'impLevelingHint', 'onnx'],
  ['vad', 'impVad', 'impVadHint', 'whisper'],
];
const IMP_AFTER = [['diarization', 'impDiarization', 'impDiarizationHint'], ['summary', 'impSummary', 'impSummaryHint']];

function impSettings() { return (state.system && state.system.settings) || {}; }

// Settings are "true"/"false" strings; a missing one is on (as on the server)
function impOn(key, s = impSettings()) { return String(s[key] ?? 'true') !== 'false'; }

function askImportParams() { return impOn('ask_import_params'); }

// What the settings choose for a new file (database.effective_processing on the server)
function impDefaults() {
  const s = impSettings();
  const sound = (key) => (s[key] != null && s[key] !== '' ? impOn(key, s) : impOn('audio_enhance', s));
  return {
    denoise: sound('proc_denoise'), loudnorm: sound('proc_loudnorm'), leveling: impOn('proc_leveling', s),
    vad: impOn('vad_filter', s), diarization: impOn('diarization_enabled', s), summary: impOn('auto_summary', s),
  };
}

function impFresh() {
  return { items: [], proc: impDefaults(), seq: 0 };
}

// "Import": the window, or the file picker at once when it should not ask
function openImport() {
  if (askImportParams()) openImportDialog();
  else clickElement('file-input');
}

// The ⌄ of the import buttons: the file picker, the window with its parameters, a link, a path
function openImportMenu(e) {
  e.stopPropagation();
  const r = e.currentTarget.getBoundingClientRect();
  openTextMenuAt([
    { label: t('impMenuFiles'), run: () => clickElement('file-input') },
    { label: t('impMenuParams'), run: () => openImportDialog() },
    { label: t('impMenuLink'), run: () => openImportDialog({ row: 'link' }) },
    { label: t('impMenuPath'), run: () => openImportDialog({ row: 'path' }) },
  ], r.left, r.bottom + 4);
}

// Opens the window with these files (dropped, chosen) or with a link/path row open
function openImportDialog(opts = {}) {
  state.imp = impFresh();
  state.impPrepared = true;
  openModal('import');
  if (opts.files) impAddFiles(opts.files);
  if (opts.row) impShowRow(opts.row);
}

// core.js openModal('import'): from any button that opens the window as it is
function onImportDialogOpen() {
  if (!state.impPrepared || !state.imp) state.imp = impFresh();
  state.impPrepared = false;
  const s = impSettings();
  const lang = document.getElementById('upload-language');
  lang.value = s.language || 'auto';
  if (!lang.value) lang.value = 'auto';
  const spk = document.getElementById('upload-speakers');
  spk.value = String(s.num_speakers || 0);
  if (!spk.value) spk.value = '0';
  document.getElementById('imp-remember').checked = false;
  document.getElementById('imp-noask').checked = !askImportParams();
  for (const id of ['imp-link-row', 'imp-path-row', 'imp-link-note']) document.getElementById(id).classList.add('hidden');
  renderModelHints();
  renderImport();
}

function impIsOpen() {
  return !document.getElementById('modal-import').classList.contains('hidden');
}

function impModelIsWhisper(id = document.getElementById('upload-model').value) {
  const item = (state.whisperCatalog || []).find(i => i.id === id);
  return item ? item.engine !== 'onnx' : !/gigaam|parakeet/i.test(id || '');
}

// --- The list: files, links, paths ---
function impExt(name) {
  const m = /\.([a-z0-9]{1,5})$/i.exec(name || '');
  return m ? m[1].toUpperCase() : '';
}

function impAddFiles(fileList) {
  const imp = state.imp || (state.imp = impFresh());
  for (const file of Array.from(fileList || [])) {
    if (imp.items.some(x => x.kind === 'file' && x.file.name === file.name && x.file.size === file.size)) continue;
    const item = { id: ++imp.seq, kind: 'file', file, dur: null };
    imp.items.push(item);
    impProbeDuration(file).then(sec => { item.dur = sec; if (impIsOpen()) renderImport(); });
  }
  document.getElementById('file-input').value = '';
  renderImport();
}

// The length of a file: read from its header by the browser (a format it cannot play gives no length)
function impProbeDuration(file) {
  return new Promise(resolve => {
    const url = URL.createObjectURL(file);
    const el = document.createElement(/^video\//.test(file.type) ? 'video' : 'audio');
    const done = (v) => { URL.revokeObjectURL(url); el.removeAttribute('src'); resolve(v); };
    el.preload = 'metadata';
    el.addEventListener('loadedmetadata', () => done(Number.isFinite(el.duration) ? el.duration : null), { once: true });
    el.addEventListener('error', () => done(null), { once: true });
    setTimeout(() => done(null), 8000);
    el.src = url;
  });
}

function impDropFiles(e) {
  e.preventDefault();
  e.stopPropagation();  // not the drop of the whole window under it
  hideDropOverlay();
  const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
  const media = files.filter(isMediaFile);
  if (media.length < files.length) {
    const skipped = files.filter(f => !isMediaFile(f)).map(f => f.name).slice(0, 3).join(', ');
    showToast(t('dropSkipped').replace('{names}', () => skipped));
  }
  if (media.length) impAddFiles(media);
}

function impShowRow(kind) {
  const row = document.getElementById(kind === 'link' ? 'imp-link-row' : 'imp-path-row');
  row.classList.remove('hidden');
  if (kind === 'link') document.getElementById('imp-link-note').classList.remove('hidden');
  setTimeout(() => row.querySelector('input').focus(), 30);
}

function impAddLink() {
  const input = document.getElementById('url-import-input');
  const url = input.value.trim();
  if (!url) return;
  if (!/^https?:\/\/\S+$/i.test(url)) { showToast(t('impBadLink')); return; }
  const imp = state.imp;
  if (!imp.items.some(x => x.kind === 'link' && x.url === url)) imp.items.push({ id: ++imp.seq, kind: 'link', url });
  input.value = '';
  renderImport();
}

function impAddPath() {
  const input = document.getElementById('local-path-input');
  const path = input.value.trim().replace(/^["']|["']$/g, '');
  if (!path) return;
  const imp = state.imp;
  if (!imp.items.some(x => x.kind === 'path' && x.path === path)) imp.items.push({ id: ++imp.seq, kind: 'path', path });
  input.value = '';
  renderImport();
}

function impRemove(id) {
  state.imp.items = state.imp.items.filter(x => x.id !== Number(id));
  renderImport();
}

function impRowHtml(x) {
  let name, sub, ext, tone;
  if (x.kind === 'file') {
    name = x.file.name;
    ext = impExt(name) || '—';
    tone = /^video\//.test(x.file.type) ? 'imp-tile-video' : 'imp-tile-audio';
    sub = t(/^video\//.test(x.file.type) ? 'impKindVideo' : 'impKindAudio');
  } else if (x.kind === 'link') {
    let host = x.url;
    try { const u = new URL(x.url); host = u.host + (u.pathname.length > 1 ? u.pathname : ''); } catch (e) {}
    name = host;
    ext = '↗';
    tone = 'imp-tile-link';
    sub = t('impKindLink');
  } else {
    name = x.path;
    ext = '▤';
    tone = 'imp-tile-path';
    sub = t('impKindPath');
  }
  const dur = x.kind === 'file' ? (x.dur != null ? formatTime(x.dur) : '…') : '';
  const size = x.kind === 'file' ? impFormatBytes(x.file.size) : '';
  return `<div class="imp-row">
      <span class="imp-tile ${tone}" aria-hidden="true">${escapeHtml(ext)}</span>
      <span class="min-w-0 flex flex-col"><span class="imp-row-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span><span class="imp-row-sub">${escapeHtml(sub)}</span></span>
      <span class="imp-row-dur">${escapeHtml(dur)}</span>
      <span class="imp-row-size">${escapeHtml(size)}</span>
      <button ${pageCall('click', 'impRemove', [x.id])} class="imp-x" title="${escapeHtml(t('impRemove'))}">✕</button>
    </div>`;
}

function impFormatBytes(n) {
  if (!n) return '';
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1).replace('.', state.uiLang === 'en' ? '.' : ',')} ${t('impGB')}`;
  return `${Math.max(1, Math.round(n / (1 << 20)))} ${t('impMB')}`;
}

// "3 файла · 2 ч 14 мин"
function impSummaryText(items) {
  if (!items.length) return t('impNothing');
  const sec = items.reduce((a, x) => a + (x.dur || 0), 0);
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  const len = !sec ? '' : !h && !m ? ` · ${t('impUnderMinute')}` : ` · ${h ? `${h} ${t('impHours')} ` : ''}${m} ${t('impMinutes')}`;
  return plural(items.length, t('impItemsForms')) + len;
}

// --- How long it will take: by the length of the files and the speed of this computer ---
function impEta(items, whisper) {
  const sec = items.reduce((a, x) => a + (x.dur || 0), 0);
  if (!sec) return null;
  const hw = (state.system && state.system.hardware) || {};
  const gpu = !!hw.cuda_available;
  // times faster than real time, roughly: what the bench measures on common cards and processors
  const speed = gpu ? (whisper ? 25 : 60) : (whisper ? 3 : 15);
  const extra = (state.imp.proc.diarization ? 1.15 : 1) * (state.imp.proc.denoise || state.imp.proc.loudnorm ? 1.05 : 1);
  const min = Math.max(1, Math.round((sec / speed) * extra / 60));
  const device = gpu ? String(hw.device_name || '').replace(/^NVIDIA (GeForce )?/, '') : t('impCpu');
  return { min, device };
}

function renderImport() {
  const imp = state.imp || (state.imp = impFresh());
  const items = imp.items;
  document.getElementById('imp-list').innerHTML = items.map(impRowHtml).join('');
  document.getElementById('imp-empty').classList.toggle('hidden', items.length > 0);
  document.getElementById('imp-summary').textContent = impSummaryText(items);
  const whisper = impModelIsWhisper();

  const eta = impEta(items, whisper);
  document.getElementById('imp-eta').classList.toggle('hidden', !eta);
  if (eta) {
    document.getElementById('imp-eta-text').innerHTML = escapeHtml(t('impEtaText')).replace('{min}', () => `<b>${eta.min} ${escapeHtml(t('impMinutes'))}</b>`)
      .replace('{device}', () => `<span class="imp-eta-dev">${escapeHtml(eta.device)}</span>`);
  }

  const card = ([key, label, hint, only]) => {
    const off = (only === 'onnx' && whisper) || (only === 'whisper' && !whisper);
    const on = !off && !!imp.proc[key];
    const tag = off ? `<span class="imp-tag">· ${escapeHtml(t(only === 'whisper' ? 'impOnlyWhisper' : 'impNotWhisper'))}</span>` : '';
    return `<label class="imp-opt${on ? ' imp-opt-on' : ''}${off ? ' imp-opt-off' : ''}">
        <span class="min-w-0 flex-1 flex flex-col gap-0.5"><span class="imp-opt-label">${escapeHtml(t(label))} ${tag}</span><span class="imp-opt-hint">${escapeHtml(t(hint))}</span></span>
        <input type="checkbox" role="switch" class="vs-switch" ${on ? 'checked' : ''} ${off ? 'disabled' : ''} ${pageCall('change', 'impToggle', [key, { $: 'checked' }])} />
      </label>`;
  };
  document.getElementById('imp-sound').innerHTML = IMP_SOUND.map(card).join('');
  document.getElementById('imp-after').innerHTML = IMP_AFTER.map(([k, l, h]) => card([k, l, h, ''])).join('');
  const def = impDefaults();
  document.getElementById('imp-reset').classList.toggle('hidden', IMP_SOUND.every(([k]) => imp.proc[k] === def[k]));

  const go = document.getElementById('imp-go');
  go.textContent = items.length ? `${t('impGo')} ${plural(items.length, t('impItemsForms'))}` : t('impGo');
  go.disabled = !items.length;
}

function impToggle(key, on) {
  state.imp.proc[key] = !!on;
  renderImport();
}

function impResetSound() {
  const def = impDefaults();
  for (const [k] of IMP_SOUND) state.imp.proc[k] = def[k];
  renderImport();
}

function impOnModel() {
  renderModelHints();
  renderImport();
}

// --- Start: each file with the chosen processing; the window stays open on an error ---
async function impSubmit() {
  const imp = state.imp;
  if (!imp.items.length || !requireRecognitionModel()) return;
  const model = document.getElementById('upload-model').value;
  const language = document.getElementById('upload-language').value;
  const speakers = Number(document.getElementById('upload-speakers').value || 0);
  const whisper = impModelIsWhisper(model);
  // what does not apply to the chosen model is sent off, so the recording shows what was done
  const processing = { ...imp.proc, leveling: !whisper && imp.proc.leveling, vad: whisper && imp.proc.vad };
  const go = document.getElementById('imp-go');
  go.disabled = true;
  go.textContent = t('impSending');
  const created = [];
  try {
    const files = imp.items.filter(x => x.kind === 'file').map(x => x.file);
    if (files.length) created.push(...await uploadFiles(files, { source: 'import', model, language, speakers, processing }));
    for (const x of imp.items.filter(i => i.kind !== 'file')) {
      const link = x.kind === 'link';
      const res = await fetch(link ? '/api/meetings/url' : '/api/meetings/local-path', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...(link ? { url: x.url } : { path: x.path }), model_name: model, language, num_speakers: speakers, processing }),
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(serverText(data.detail) || `HTTP ${res.status}`);
      created.push(...(data.created || []));
      imp.items = imp.items.filter(i => i !== x);  // what went is not sent twice if a later one fails
    }
  } catch (err) {
    showToast(humanError(err.message || err).short);
    renderImport();
    if (created.length) await loadMeetingsList();
    return;
  }
  const remember = document.getElementById('imp-remember').checked;
  const noAsk = document.getElementById('imp-noask').checked;
  const values = {};
  if (remember) {
    Object.assign(values, {
      whisper_model: model, language, num_speakers: String(speakers),
      proc_denoise: String(imp.proc.denoise), proc_loudnorm: String(imp.proc.loudnorm), proc_leveling: String(imp.proc.leveling),
      audio_enhance: String(imp.proc.denoise || imp.proc.loudnorm), vad_filter: String(imp.proc.vad),
      diarization_enabled: String(imp.proc.diarization), auto_summary: String(imp.proc.summary),
    });
  }
  if (noAsk === askImportParams()) values.ask_import_params = String(!noAsk);
  if (Object.keys(values).length) await impSaveSettings(values);
  closeModal('import');
  state.imp = impFresh();
  if (!created.length) return;
  switchAppView('library');
  await loadMeetingsList();
  await selectMeeting(created[0].id, true);
  showToast(t('impQueued').replace('{n}', () => plural(created.length, t('impItemsForms'))));
}

async function impSaveSettings(values) {
  const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: values }) });
  if (!res.ok) { showToast(serverText((await readJson(res)).detail) || 'Error'); return; }
  if (state.system) state.system.settings = { ...(state.system.settings || {}), ...values };
}
