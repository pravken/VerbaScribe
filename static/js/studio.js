// VerbaScribe page script, part 7 of 9: Recording: sources, presets, screen capture, live captions, the studio.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

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
      const label = m.label || t('micNumbered').replace('{n}', () => i + 1);
      const sel = m.deviceId === savedMic ? 'selected' : '';
      return `<option value="${escapeHtml(m.deviceId)}" ${sel}>${escapeHtml(label)}</option>`;
    }).join('');

    if (settingsSelect) settingsSelect.innerHTML = optionsHtml || `<option value="">Default Microphone</option>`;
    if (modalSelect) modalSelect.innerHTML = optionsHtml || `<option value="">Default Microphone</option>`;
  } catch (e) {
    const fallback = `<option value="">${t('uiDefaultSystemMicrophone')}</option>`;
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
        t('uiPleaseEnableShareSystem')
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
        ? t('uiSignalDetectedAudioIs')
        : t('uiListeningSpeakToTest');

      _settingsTestAnim = requestAnimationFrame(step);
    }
    step();
  } catch (err) {
    stopSettingsAudioTest();
    showAlert(err.message || err);
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
  const name = (await askText(t('presetNamePrompt'), '') || '').trim();
  if (!name) return;
  const own = recordPresets().filter(p => !p.builtin);
  const preset = presetFromForm({ id: `p${Date.now().toString(36)}`, name: name.slice(0, 60) });
  own.push(preset);
  await saveRecordPresetSettings(own);
  state.recPresetId = preset.id;
  renderRecordPresetSelect(preset.id);
  showToast(t('presetSaved').replace('{name}', () => preset.name));
}

async function updateRecordPreset() {
  const id = document.getElementById('record-preset-select').value;
  const own = recordPresets().filter(p => !p.builtin).map(p => (p.id === id ? presetFromForm(p) : p));
  await saveRecordPresetSettings(own);
  renderRecordPresetSelect(id);
  showToast(t('presetSaved').replace('{name}', () => presetLabel(own.find(p => p.id === id) || {})));
}

async function deleteRecordPreset() {
  const id = document.getElementById('record-preset-select').value;
  const p = recordPresets().find(x => x.id === id);
  if (!p || p.builtin || !await askConfirm(t('presetDeleteConfirm').replace('{name}', () => p.name), true)) return;
  const own = recordPresets().filter(x => !x.builtin && x.id !== id);
  await saveRecordPresetSettings(own, defaultRecordPresetId() === id ? 'call' : null);
  applyRecordPreset(defaultRecordPresetId());
}

async function setDefaultRecordPreset() {
  const id = document.getElementById('record-preset-select').value;
  await saveRecordPresetSettings(recordPresets().filter(p => !p.builtin), id);
  renderRecordPresetSelect(id);
  showToast(t('presetDefaultSet').replace('{name}', () => presetLabel(recordPresets().find(p => p.id === id) || {})));
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
    `hideRecordPresetMenu(); quickRecord(${jsArg(p.id)})`, p.id === def ? '★' : '', presetLabel(p),
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
  return !!(_mediaRecorder && _mediaRecorder.state === 'recording') || !!_recCaptureId || !!_recScreenId;
}

// --- The screen or a window recorded by the app (FFmpeg), when the window's picker cannot be used ---
let _recScreenId = null;
state.screenCapture = { supported: false, windows: [] };

async function loadScreenWindows() {
  try {
    const data = await fetch('/api/record/screen/windows').then(r => r.json());
    state.screenCapture = { supported: !!data.supported, windows: data.windows || [] };
  } catch (e) {}
  document.querySelectorAll('.rec-app-video').forEach(o => o.classList.toggle('hidden', !state.screenCapture.supported));
  const sel = document.getElementById('record-window-select');
  const keep = sel.value;
  sel.innerHTML = state.screenCapture.windows.length
    ? state.screenCapture.windows.map(w => `<option value="${escapeHtml(w.title)}">${escapeHtml(w.title)}${w.minimized ? ' · ' + escapeHtml(t('recWindowMinimized')) : ''}</option>`).join('')
    : `<option value="">${escapeHtml(t('recNoWindows'))}</option>`;
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
}

function syncRecordWindowSelect() {
  const v = document.getElementById('record-video-select').value;
  const sel = document.getElementById('record-window-select');
  sel.classList.toggle('hidden', v !== 'app_window');
  if (v === 'app_window') loadScreenWindows();
}

async function startAppScreenCapture(target) {
  const res = await fetch('/api/record/screen/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || t('recVideoFailed'));
  _recScreenId = data.screen_id;
}

// --- Calls recorded by themselves (app/call_watch.py): a watched app took or let go of the microphone ---
state.callRecording = null;

async function handleCallEvent(ev) {
  if (ev.state === 'started') {
    if (isRecording()) return;  // already recording by hand: that recording is left alone
    const s = (state.system && state.system.settings) || {};
    state.callRecording = ev.app;
    showToast(t('callRecordStarted').replace('{app}', () => ev.name || ev.app));
    try {
      await quickRecord(s.call_preset || defaultRecordPresetId());
    } catch (e) {
      showToast(`${t('callRecordFailed')}: ${e.message || e}`);
    }
    // The recording starts asynchronously: wait for it before deciding it did not start
    for (let i = 0; i < 40 && !isRecording(); i++) await new Promise(r => setTimeout(r, 250));
    if (!isRecording()) state.callRecording = null;
  } else if (ev.state === 'ended' && state.callRecording) {
    state.callRecording = null;
    if (!isRecording()) return;
    showToast(t('callRecordEnded').replace('{app}', () => ev.name || ev.app));
    stopAndTranscribeRecording();
  }
}

// --- Questions during a call: answered from the live captions so far ---
async function studioAsk() {
  const input = document.getElementById('studio-ask-input');
  const q = input.value.trim();
  if (!q || !liveCap.session) return;
  const box = document.getElementById('studio-ask-answer');
  const btn = document.getElementById('studio-ask-btn');
  btn.disabled = true;
  box.classList.remove('hidden');
  box.innerHTML = `<div class="text-apple-tertiary animate-pulse">${escapeHtml(t('libraryAskWorking'))}</div>`;
  try {
    const res = await fetch(`/api/live/${liveCap.session}/ask`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, lang: state.uiLang === 'en' ? 'en' : 'ru' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Error');
    box.innerHTML = `<div class="text-[11px] text-apple-tertiary mb-1">${escapeHtml(q)}</div>${sanitizedMarkdown(data.answer)}`;
    input.value = '';
  } catch (e) {
    box.innerHTML = `<div class="text-apple-amber">${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function syncStudioAsk() {
  const el = document.getElementById('studio-ask');
  if (el) el.classList.toggle('hidden', !liveCap.session);
  document.getElementById('studio-notes')?.classList.toggle('hidden', !isRecording());
}

// --- My notes while recording: Enter starts a new line with the recording time ---
function studioNotesStamp() {
  const sec = _recStartTime ? Math.floor((Date.now() - _recStartTime) / 1000) : 0;
  return `[${formatTime(sec)}] `;
}

function studioNotesKey(e) {
  const ta = e.target;
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const pos = ta.selectionStart;
    const ins = (pos > 0 ? '\n' : '') + studioNotesStamp();
    ta.value = ta.value.slice(0, pos) + ins + ta.value.slice(ta.selectionEnd);
    ta.selectionStart = ta.selectionEnd = pos + ins.length;
    saveStudioNotesDraft();
  } else if (!ta.value && e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
    ta.value = studioNotesStamp();  // the first note gets its time too
  }
}

function saveStudioNotesDraft() {
  try { localStorage.setItem('verbascribe_studio_notes', document.getElementById('studio-notes-input').value); } catch (e) {}
}

function takeStudioNotes() {
  const ta = document.getElementById('studio-notes-input');
  const v = ta ? ta.value : '';
  if (ta) ta.value = '';
  try { localStorage.removeItem('verbascribe_studio_notes'); } catch (e) {}
  return v;
}

async function quickRecord(presetId) {
  if (isRecording()) {
    switchAppView('studio');  // already recording: back to the studio
    return;
  }
  await openRecordModal(presetId || defaultRecordPresetId());
  await startLiveRecording();
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
    if (!document.getElementById('record-video-select').value) document.getElementById('record-video-select').value = '';
    loadScreenWindows().then(syncRecordWindowSelect);
  }
  if (!_studioPrepared || presetId) applyRecordPreset(presetId || state.recPresetId || defaultRecordPresetId());
  else renderRecordPresetSelect(state.recPresetId || defaultRecordPresetId());
  renderRecordSaveHint();

  const titleInput = document.getElementById('record-title-input');
  if (!titleInput.value.trim()) {
    const now = new Date();  // 24-hour HH-MM in any language: the title becomes a file name
    const nowStr = `${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}`;
    titleInput.value = `${t('recordDefaultTitle')} ${nowStr}`;
  }
  document.getElementById('record-timer-display').textContent = '00:00';
  document.getElementById('record-level-bar').style.width = '2%';
  document.getElementById('record-db-display').textContent = '-60 dB';
  document.getElementById('record-signal-dot').classList.remove('on');
  _studioPrepared = true;
  renderStudioState();
  setTimeout(() => drawIdleSoundCloudBars('record-waveform-canvas'), 40);
  loadUpcomingMeetings();
}

// --- The next meetings of the user's calendar (app/calendar_ics.py), shown while the studio is idle ---
let _upcoming = [];

async function loadUpcomingMeetings() {
  const st = await fetch('/api/calendar/upcoming').then(r => r.json()).catch(() => null);
  _upcoming = st && st.configured ? st.events || [] : [];
  renderUpcomingMeetings();
}

function renderUpcomingMeetings() {
  const box = document.getElementById('studio-upcoming');
  if (!box) return;
  const show = _upcoming.length && !isRecording() && !(capView.rows.size > 0);
  box.classList.toggle('hidden', !show);
  if (!show) return;
  const now = new Date();
  const today = now.toDateString();
  const rows = _upcoming.map((e, i) => {
    const start = new Date(e.start), end = new Date(e.end);
    const time = start.toLocaleTimeString(state.uiLang === 'en' ? 'en-US' : 'ru-RU', { hour: '2-digit', minute: '2-digit' });
    const day = start.toDateString() === today ? '' : `${start.toLocaleDateString(state.uiLang === 'en' ? 'en-US' : 'ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })} · `;
    const live = start <= now && now < end;
    const people = e.attendees.length ? `<div class="text-[11px] text-apple-tertiary truncate">${escapeHtml(e.attendees.slice(0, 6).join(', '))}${e.attendees.length > 6 ? ` +${e.attendees.length - 6}` : ''}</div>` : '';
    const brief = e.series_id || e.person
      ? `<button onclick="upcomingBrief(${i})" class="ui-btn ui-btn-subtle h-7 px-2.5 text-[11px]" title="${escapeHtml(t('tipUpcomingBrief'))}">${escapeHtml(t('briefBtn'))}</button>` : '';
    return `<div class="flex items-center gap-3 px-3 py-2 rounded-lg ${live ? 'bg-apple-red/10' : 'hover:bg-white/5'}">
      <div class="w-32 shrink-0 text-[11px] font-mono whitespace-nowrap ${live ? 'text-apple-red' : 'text-apple-secondary'}">${escapeHtml(day + time)}</div>
      <div class="flex-1 min-w-0"><div class="text-[13px] text-white truncate">${escapeHtml(e.title || '—')}</div>${people}</div>
      ${brief}
      <button onclick="upcomingUseTitle(${i})" class="ui-btn ui-btn-subtle h-7 px-2.5 text-[11px]" title="${escapeHtml(t('tipUpcomingTitle'))}">${escapeHtml(t('upcomingUseTitle'))}</button>
    </div>`;
  }).join('');
  box.innerHTML = `<div class="text-[11px] uppercase tracking-wider text-apple-tertiary mb-1.5 px-3">${escapeHtml(t('upcomingTitle'))}</div>${rows}`;
}

function upcomingUseTitle(i) {
  const e = _upcoming[i];
  if (!e) return;
  document.getElementById('record-title-input').value = e.title || '';
  showToast(t('upcomingTitleSet'));
}

function upcomingBrief(i) {
  const e = _upcoming[i];
  if (!e) return;
  state.askScope = e.series_id ? { type: 'series', id: e.series_id } : { type: 'person', name: e.person };
  switchAppView('ask');
  if (typeof renderAskScope === 'function') renderAskScope();
  makeBrief();
}

function saveRecordVideoPref() {
  try { localStorage.setItem('verbascribe_record_video', document.getElementById('record-video-select').value); } catch (e) {}
  syncRecordWindowSelect();
  renderStudioState();
}

function renderRecordSaveHint() {
  const dir = state.system && state.system.paths && state.system.paths.recordings;
  document.getElementById('record-save-hint').textContent = dir ? t('recordSaveHint').replace('{dir}', () => dir) : '';
}

// --- Live captions while recording ---
// The same mixed stream that is recorded is tapped by an AudioWorklet, resampled to 16 kHz mono
// 16-bit PCM and posted about once a second; each answer carries new final lines, their
// translations and the current draft line.
const liveCap = { session: null, node: null, pcm: [], pcmLen: 0, inFlight: false, timer: null, lines: new Map(), partial: '', lastId: 0 };

function liveCaptionPrefs() {
  try { return JSON.parse(localStorage.getItem('verbascribe_live_captions') || '{}'); } catch (e) { return {}; }
}

// The model pickers above the captions: "Auto · <model in use>" and the downloaded models
function fillModelSelect(sel, options, value) {
  const html = options.map(([v, label]) => `<option value="${escapeHtml(v)}">${escapeHtml(label)}</option>`).join('');
  if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
  if (document.activeElement !== sel) sel.value = options.some(o => o[0] === value) ? value : options[0][0];
}

function renderStudioModels(target) {
  const box = document.getElementById('studio-models');
  box.classList.toggle('hidden', !liveCap.session);
  if (!liveCap.session) return;
  const auto = t('liveModelAuto');
  const capChoice = liveCap.captionChoice || 'auto';
  fillModelSelect(document.getElementById('studio-cap-model'), [
    ['auto', capChoice === 'auto' && liveCap.captionModel ? `${auto} · ${liveCap.captionModel}` : auto],
    ...(state.onnxInstalled || []).map(id => [id, formatModelName(id)]),
    ...(state.whisperInstalled || []).map(id => [id, formatModelName(id)]),
  ], capChoice);
  document.getElementById('studio-tr-model-wrap').classList.toggle('hidden', !target);
  if (!target) return;
  const trChoice = liveCap.translateChoice || '';
  fillModelSelect(document.getElementById('studio-tr-model'), [
    ['', !trChoice && liveCap.translateModel ? `${auto} · ${liveCap.translateModel}` : auto],
    ...(studio.llmModels || []).map(m => [m, formatModelName(m)]),
  ], trChoice);
  if (!studio.llmModels) {
    studio.llmModels = [];
    fetch('/api/live/models').then(r => r.json()).then(d => { studio.llmModels = d.installed || []; syncStudioCaptions(); }).catch(() => {});
  }
}

async function studioSetModels(change) {
  if (!liveCap.session) return;
  if ('caption_model' in change) { liveCap.captionChoice = change.caption_model; state.liveModelSetting = change.caption_model; }
  if ('translate_model' in change) liveCap.translateChoice = change.translate_model;
  try {
    const res = await fetch(`/api/live/${liveCap.session}/config`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ translate_to: document.getElementById('live-translate-select').value || null, pair: quickLangs(), ...change }),
    });
    if (!res.ok) showToast((await res.json().catch(() => ({}))).detail || 'Error');
  } catch (e) { console.error(e); }
  syncStudioCaptions();
}

function liveDraftOn() {
  return !!document.getElementById('live-draft-toggle')?.checked;
}

function liveDraftTrOn() {
  return !!document.getElementById('live-draft-tr-toggle')?.checked;
}

function saveLiveCaptionPrefs() {
  const prefs = {
    on: document.getElementById('live-captions-toggle').checked,
    translate: document.getElementById('live-translate-select').value,
    draft: document.getElementById('live-draft-toggle').checked,
    draftTr: document.getElementById('live-draft-tr-toggle').checked,
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
  document.getElementById('live-draft-toggle').checked = !!prefs.draft;
  document.getElementById('live-draft-tr-toggle').checked = !!prefs.draftTr;
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

// The interface language changed: the language lists are written in it
function relabelStudioLanguages() {
  const tr = document.getElementById('live-translate-select');
  const speech = document.getElementById('studio-speech');
  if (!tr || !tr.options.length) return;
  const codes = _textLangs || TEXT_LANG_FALLBACK;
  fillTranslateSelect(codes, tr.value);
  const value = speech.value;
  speech.innerHTML = `<option value="auto">${escapeHtml(t('studioSpeechAuto'))}</option>` + textLangOptions(codes, true);
  speech.value = value;
  renderQuickLangs();
}

function fillTranslateSelect(codes, value) {
  const sel = document.getElementById('live-translate-select');
  sel.innerHTML = `<option value="">${escapeHtml(t('liveNoTranslation'))}</option>`
    + `<option value="auto">${escapeHtml(t('liveAutoPair').replace('{pair}', () => autoPairLabel()))}</option>`
    + textLangOptions(codes, false);
  sel.value = [...sel.options].some(o => o.value === value) ? value : '';
}

function renderQuickLangs() {
  const box = document.getElementById('studio-quick-langs');
  if (!box) return;
  const current = document.getElementById('live-translate-select').value;
  const [a, b] = quickLangs();
  const btn = (value, label, title, menuIndex) =>
    `<button onclick="setQuickTranslate(${jsArg(value)})" ${menuIndex != null ? `oncontextmenu="openQuickLangMenu(event, ${menuIndex})"` : ''}
      class="quick-lang ${current === value ? 'on' : ''}" title="${escapeHtml(title)}">${escapeHtml(label)}</button>`;
  box.innerHTML = btn(a, langCode(a), t('tipQuickLang').replace('{lang}', () => langName(a)), 0)
    + btn(b, langCode(b), t('tipQuickLang').replace('{lang}', () => langName(b)), 1)
    + btn('auto', `⇄ ${t('liveAutoShort')}`, t('tipQuickAuto').replace('{a}', () => langName(a)).replace('{b}', () => langName(b)), null);
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
      + `<span>${escapeHtml(n)}</span><span class="text-[10.5px] opacity-80">${c === langs[index] ? '✓' : escapeHtml(langCode(c))}</span></button>`).join('');
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
        draft: liveDraftTrOn(),
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
    liveCap.captionModel = data.caption_model || '';
    liveCap.translateModel = data.translate_model || '';
    liveCap.captionChoice = data.caption_choice || 'auto';
    liveCap.translateChoice = data.translate_choice || '';
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
  syncStudioAsk();
  const sourceName = { mic: t('sourceMic'), system: t('sourceSystem'), both: t('sourceBoth') };
  const p = recordPresets().find(x => x.id === state.recPresetId);
  const withVideo = !!document.getElementById('record-video-select').value;
  document.getElementById('studio-source').textContent =
    [p ? presetLabel(p) : '', sourceName[document.getElementById('record-source-select').value] || '', withVideo ? t('recVideoShort') : '']
      .filter(Boolean).join(' · ');
  document.getElementById('studio-heading').textContent = t(rec ? 'studioRecording' : 'studioIdle');
  document.getElementById('studio-rec-dot').classList.toggle('studio-idle', !rec);
  for (const id of ['studio-start-btn', 'btn-start-rec']) document.getElementById(id).classList.toggle('hidden', rec);
  for (const id of ['studio-stop-btn', 'studio-cancel-btn', 'btn-stop-rec', 'btn-cancel-rec']) document.getElementById(id).classList.toggle('hidden', !rec);
  // What is recorded cannot change mid-recording; how captions look can
  for (const id of ['record-preset-select', 'record-source-select', 'record-mic-select', 'record-video-select', 'record-window-select', 'record-title-input']) {
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
  ['quality', 'fast'].forEach(k => {
    document.getElementById(`studio-speed-${k}`).className = `apple-segment-btn text-[11px] ${studio.speed === k ? 'apple-segment-active' : 'text-apple-secondary hover:text-white'}`;
  });
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
      body: JSON.stringify({ translate_to: target, pair: quickLangs(), speed: studio.speed, speech_lang: speechChanged ? studio.speech : null, draft: liveDraftTrOn() }),
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
  syncStudioAsk();
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
    // Optional: the phrase as it is spoken (otherwise only "…" until it is final)
    row.orig.set(liveDraftOn() ? liveCap.partial : '');
    row.tr.set(liveDraftTrOn() ? (liveCap.partialTranslation || '') : '');
    row.el.classList.toggle('cap-draft-live', liveDraftTrOn());
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
  renderUpcomingMeetings();
  capFollowBottom();
  renderStudioControls();
  const status = document.getElementById('studio-status');
  renderStudioModels(target);
  status.textContent = liveCap.error ? liveCap.error
    : target && liveCap.session && !liveCap.translationReady ? t('studioLoadingTranslator') : '';
  status.className = `text-[11px] ${liveCap.error ? 'text-apple-amber' : 'text-apple-tertiary'}`;
}

async function startLiveRecording() {
  if (isRecording()) return;
  try {
    const sourceMode = document.getElementById('record-source-select').value || 'mic';
    const micId = document.getElementById('record-mic-select').value || '';
    syncRecordModalToSettings();

    // Video first: the screen picker must open right after the click, before anything is awaited
    _recVideoStream = null;
    _recScreenId = null;
    const videoMode = document.getElementById('record-video-select').value;
    // The screen recorded by the app: chosen, or when the window cannot show its own picker
    let appTarget = videoMode === 'app_desktop' ? 'desktop'
      : videoMode === 'app_window' ? document.getElementById('record-window-select').value : null;
    if (videoMode === 'app_window' && !appTarget) throw new Error(t('recNoWindows'));
    if (videoMode === 'screen') {
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) throw new Error('getDisplayMedia');
        _recVideoStream = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: { ideal: 30, max: 30 }, width: { max: 1920 }, height: { max: 1080 } },
          audio: false,
        });
      } catch (e) {
        if (e && e.name === 'NotAllowedError') return;  // the picker was closed: nothing starts
        if (!state.screenCapture.supported) throw new Error(t('recVideoFailed') + (e && e.message ? `: ${e.message}` : ''));
        appTarget = 'desktop';
        showToast(t('recVideoFallback'));
      }
    }
    if (_recVideoStream) {
      const preview = document.getElementById('record-video-preview');
      preview.srcObject = _recVideoStream;
      preview.classList.remove('hidden');
      // Sharing stopped from the system bar: the sound keeps recording, the picture ends there
      _recVideoStream.getVideoTracks()[0].addEventListener('ended', () => {
        preview.classList.add('hidden');
        showToast(t('recVideoEnded'));
      });
    }

    // Free space: a recording that fills the disk can lose the file it was writing
    const disk = await fetch('/api/disk').then(r => r.json()).catch(() => null);
    if (disk && disk.critical) {
      _recVideoStream?.getTracks().forEach(tr => tr.stop());
      throw new Error(t('diskFullError').replace('{free}', () => formatSize(Math.max(0, disk.free_bytes))));
    }
    if (disk && disk.low && !await askConfirm(t('diskLowConfirm')
      .replace('{free}', () => formatSize(disk.free_bytes))
      .replace('{min}', () => recMinutesLeft(disk.free_bytes, !!_recVideoStream || !!appTarget)))) {
      _recVideoStream?.getTracks().forEach(tr => tr.stop());
      document.getElementById('record-video-preview').classList.add('hidden');
      return;
    }
    _recDiskWarned = false;
    if (appTarget) await startAppScreenCapture(appTarget);

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
    // Notes left from a recording that did not finish (the window was closed) are kept
    try {
      const draft = localStorage.getItem('verbascribe_studio_notes');
      const ta = document.getElementById('studio-notes-input');
      if (ta && draft && !ta.value) ta.value = draft;
    } catch (e) {}
    renderStudioState();
    clearInterval(_recDiskTimer);
    _recDiskTimer = setInterval(checkRecordingDisk, 15000);
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
    showAlert(t('recCaptureError') + err.message);
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

// About how many minutes of recording fit into this much space (video ~25 MB/min with the system
// audio capture, sound only ~12 MB/min: the system audio is captured uncompressed)
function recMinutesLeft(bytes, video) {
  return Math.max(0, Math.floor((bytes - 300 * 1048576) / ((video ? 25 : 12) * 1048576)));
}

let _recDiskTimer = null;
let _recDiskWarned = false;

async function checkRecordingDisk() {
  if (!isRecording()) return;
  const disk = await fetch('/api/disk').then(r => r.json()).catch(() => null);
  if (!disk || disk.free_bytes < 0) return;
  if (disk.critical) {
    // Stopped while there is still room to finish the file and save it
    showToast(t('diskStopped'));
    stopAndTranscribeRecording();
  } else if (disk.low && !_recDiskWarned) {
    _recDiskWarned = true;
    showToast(t('diskLowWarn').replace('{free}', () => formatSize(disk.free_bytes))
      .replace('{min}', () => recMinutesLeft(disk.free_bytes, !!_recVideoStream)));
  }
}

function _cleanupRecordingResources() {
  if (_recScreenId) {  // a start that failed half way: the screen capture must not keep running
    fetch(`/api/record/screen/${_recScreenId}/cancel`, { method: 'POST' }).catch(() => {});
    _recScreenId = null;
  }
  clearInterval(_recDiskTimer);
  _recDiskTimer = null;
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

async function cancelLiveRecording() {
  if (!isRecording()) return;
  if (!await askConfirm(t('recCancelConfirm'), true)) return;
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
  if (_recScreenId) {
    fetch(`/api/record/screen/${_recScreenId}/cancel`, { method: 'POST' }).catch(() => {});
    _recScreenId = null;
  }
  _cleanupRecordingResources();
  prepareStudio();
}

async function stopAndTranscribeRecording() {
  state.callRecording = null;
  if (!_mediaRecorder && !_recCaptureId && !_recScreenId) return;
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
  const screenId = _recScreenId;
  _recScreenId = null;
  const rawTitle = (document.getElementById('record-title-input').value || t('recordDefaultTitle')).trim();
  const startedAt = _recStartTime ? _recStartTime / 1000 : 0;
  _cleanupRecordingResources();

  if (page) await page.upload;  // every chunk is on the server
  if (page && page.failed) showToast(page.failed);
  const res = await fetch('/api/record/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: rawTitle, page_id: page ? page.id : null, capture_id: captureId, screen_id: screenId,
      has_video: !!(page && page.hasVideo), has_mic: !!(page && page.hasMic),
      notes: takeStudioNotes(), started_at: startedAt,
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
