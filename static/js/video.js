// VerbaScribe page script, part 8 of 9: Video, theatre mode, subtitles over the video, keyboard shortcuts.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

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
  } else if (e.code === 'Space' && hasMedia && !e.target.closest?.('button, a, [role="button"], [role="tab"], label')) {
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
  } else if (e.code === 'KeyR' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    // One key starts capturing the microphone and system sound: asked first, not started at once
    e.preventDefault();
    if (isRecording()) return;
    showToast(t('quickRecordAsk'), { label: t('quickRecordStart'), onClick: () => quickRecord() });
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
function releaseWillowFluff(count, spread, origin) {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const logo = origin || document.getElementById('app-logo');
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
