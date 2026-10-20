// VerbaScribe page script, part 9 of 9: Updates, start-up, analysis and chapters, export, folders, Obsidian, details, the text menu.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

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
