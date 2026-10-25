// VerbaScribe page script: the player and the editor of the redesign (package A, docs/redesign-plan.md).
// The parts are classic scripts sharing one global scope, loaded in order by index.html.
//
// The wave: 180 bars of the recording's loudness in the colour of whoever speaks at their moment,
// bright behind the playhead and dimmed ahead; the phrase being played is framed, the chapters are
// bands with their titles. Under it: who speaks now and what (the words light up as they are said),
// the transport, «Звук» (studio voice, skipping silence, the track), the speed, the volume, ⋯.

const RP_BARS = 180;
const RP_SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const rp = {
  peaks: [],        // RP_BARS values 0…1, smoothed
  barSpk: [],       // the speaker of each bar
  pauses: [],       // [start, end] without speech («Пропускать тишину»)
  pauseSec: 0,
  fillers: null,    // what «Без запинок» hides (text_utils.filler_view)
  open: null,       // the open popover: 'sound' | 'speed' | 'more'
  remain: false,    // the end time shows what is left
  loopSeg: null,    // the phrase played in a loop
  ab: null,         // { a, b }: A–B repeat (b null while only A is set)
  marks: [],        // moments marked with M (seconds), kept per recording
  hover: -1,        // the ratio under the mouse
  drag: false,
  skipped: 0,       // seconds jumped over in this recording
  saveAudioOpen: false,
  meetingId: null,
  raf: 0,
  skipTimer: 0,
};

function rpPlayer() { return document.getElementById('media-player'); }
function rpDuration() {
  const p = rpPlayer();
  return (p && isFinite(p.duration) && p.duration) || (state.activeMeeting && state.activeMeeting.duration) || 0;
}
function rpSegments() { return (state.activeMeeting && state.activeMeeting.segments) || []; }
const RP_COLORS = ['#64D2FF', '#FFB340', '#C9A2FF', '#7EE787', '#FF8A80', '#5AC8FA', '#FFD60A', '#BF5AF2'];
function rpSpeaker(id) {
  const all = (state.activeMeeting && state.activeMeeting.speakers) || {};
  const s = all[id];
  const keys = Object.keys(all);
  const fallback = RP_COLORS[Math.max(0, keys.indexOf(id)) % RP_COLORS.length];
  if (!s) return { name: id || '', color: fallback };
  if (typeof s === 'string') return { name: s, color: fallback };
  return { name: s.name || id || '', color: /^#[0-9a-f]{6}$/i.test(s.color || '') ? s.color : fallback };
}

// The phrase playing at `t` (the last one that started), or -1 before the first
function rpSegIndexAt(t) {
  const segs = rpSegments();
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if ((Number(segs[mid].start) || 0) <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

function rpHexA(hex, a) {
  const m = String(hex || '').match(/^#?([0-9a-f]{6})$/i);
  if (!m) return `rgba(10,132,255,${a})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// --- Loading a recording into the player ---
// Called by buildPlayerWaveformEnvelope (player.js) each time a recording is opened
async function rpLoadMeeting(meeting) {
  const id = meeting ? meeting.id : null;
  // «Без запинок» starts as the setting says (Settings → Recognition), then follows the button
  if (state.noFillers === undefined) state.noFillers = ((state.system && state.system.settings) || {}).clean_fillers !== 'false';
  if (rp.meetingId !== id) {
    rp.meetingId = id;
    rp.loopSeg = null;
    rp.ab = null;
    rp.skipped = 0;
    rp.fillers = null;
    rp.pauses = [];
    rp.pauseSec = 0;
    rp.marks = rpLoadMarks(id);
    rpUpdateLoopButton();
  }
  rp.peaks = new Array(RP_BARS).fill(0.14);
  rpComputeBarSpeakers();
  rpRender();
  if (!meeting) return;
  if (!meeting.media_removed) {
    try {
      const res = await fetch(`/api/meetings/${id}/peaks?bars=${RP_BARS}`);
      const data = res.ok ? await readJson(res) : {};
      if (rp.meetingId === id && Array.isArray(data.peaks) && data.peaks.length) rp.peaks = rpSmooth(data.peaks);
    } catch (e) {}
  }
  rpLoadTextViews(meeting);
  rpComputeBarSpeakers();
  rpRender();
  rpRenderSoundChip();
  rpRenderVideoButton();
}

// The pauses to skip and the text without fillers follow the transcript (reloaded when it changes)
async function rpLoadTextViews(meeting) {
  const id = meeting.id;
  if (!(meeting.segments || []).length) return;
  try {
    const [p, f] = await Promise.all([
      fetch(`/api/meetings/${id}/pauses`).then(r => (r.ok ? r.json() : null)),
      fetch(`/api/meetings/${id}/fillers-view`).then(r => (r.ok ? r.json() : null)),
    ]);
    if (rp.meetingId !== id) return;
    if (p) { rp.pauses = p.pauses || []; rp.pauseSec = p.seconds || 0; }
    if (f) rp.fillers = f;
    rpRenderFillerButton();
    // the view without fillers is drawn again, but not over a line being edited: the next drawing
    // (its «Сохранить» / «Отмена») shows it
    const editing = document.querySelector('#transcript-inner textarea, #transcript-inner [contenteditable="true"]');
    if (state.noFillers && !editing && typeof renderTranscript === 'function') renderTranscript();
    rpRender();
  } catch (e) {}
}

// The transcript was drawn again (opened, edited, a speaker renamed): the wave's colours follow it,
// and after an edit the pauses and the filler view are asked again
function rpOnTranscript() {
  const m = state.activeMeeting;
  rpComputeBarSpeakers();
  rpRender();
  if (!m) return;
  const segs = m.segments || [];
  const sig = `${m.id}:${segs.length}:${segs.reduce((n, s) => n + (s.text || '').length + (s.words || []).length, 0)}`;
  if (rp.segSig === sig) return;
  const first = !rp.segSig || !rp.segSig.startsWith(`${m.id}:`);
  rp.segSig = sig;
  if (first) return;  // rpLoadMeeting asks for them when a recording is opened
  clearTimeout(rp.textTimer);
  // by id: the list's poll may have put a new object of the same recording in its place meanwhile
  rp.textTimer = setTimeout(() => { if (state.activeMeeting && state.activeMeeting.id === m.id) rpLoadTextViews(state.activeMeeting); }, 600);
}

// Speech loudness rises and falls gradually: smooth the noise so the wave has soft hills, not steps,
// keep a little of the raw texture, and stretch it back to the full range
function rpSmooth(raw) {
  const src = raw.map(v => Math.max(0, Number(v) || 0));
  const out = src.map((_, i) => {
    let sum = 0, w = 0;
    [1, 2, 3, 2, 1].forEach((k, j) => { const x = src[i + j - 2]; if (x !== undefined) { sum += x * k; w += k; } });
    return (sum / w) * 0.8 + src[i] * 0.2;
  });
  const max = Math.max(0.05, ...out);
  return out.map(v => Math.max(0.08, Math.min(1, v / max)));
}

function rpComputeBarSpeakers() {
  const segs = rpSegments();
  const dur = rpDuration() || 1;
  rp.barSpk = Array.from({ length: RP_BARS }, (_, i) => {
    const idx = rpSegIndexAt(((i + 0.5) / RP_BARS) * dur);
    return idx >= 0 ? segs[idx].speaker : (segs[0] && segs[0].speaker) || null;
  });
}

// --- Drawing ---
function rpRender() {
  const canvas = document.getElementById('player-waveform-canvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const z = (typeof uiZoom === 'function' ? uiZoom() : 1) || 1;
  const w = Math.max(200, rect.width / z || 540);
  const h = Math.max(40, rect.height / z || 76);
  const dpr = (window.devicePixelRatio || 1) * z;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const p = rpPlayer();
  const dur = rpDuration();
  const cur = (p && p.currentTime) || 0;
  const ratio = dur > 0 ? Math.min(1, cur / dur) : 0;
  const x = (t) => (dur > 0 ? (t / dur) * w : 0);
  const segs = rpSegments();
  const idx = rpSegIndexAt(cur);
  const curSeg = idx >= 0 ? segs[idx] : null;
  const curColor = curSeg ? rpSpeaker(curSeg.speaker).color : '#0A84FF';

  // Chapter bands, every other one a shade lighter, a dark line at each start
  const chapters = rpChapters();
  chapters.forEach((c, i) => {
    const a = x(c.start), b = i + 1 < chapters.length ? x(chapters[i + 1].start) : w;
    if (i % 2 === 0) { ctx.fillStyle = 'rgba(255,255,255,0.025)'; ctx.fillRect(a, 0, b - a, h); }
    if (i > 0) { ctx.fillStyle = '#0B0C0F'; ctx.fillRect(a, 0, 2, h); }
  });
  // What has been played, a little lighter; a trail of the speaker's colour behind the playhead
  ctx.fillStyle = 'rgba(255,255,255,0.025)';
  ctx.fillRect(0, 0, ratio * w, h);
  const trail = Math.min(ratio * w, w * 0.06);
  if (trail > 1) {
    const g = ctx.createLinearGradient(ratio * w - trail, 0, ratio * w, 0);
    g.addColorStop(0, rpHexA(curColor, 0));
    g.addColorStop(1, rpHexA(curColor, 0.18));
    ctx.fillStyle = g;
    ctx.fillRect(ratio * w - trail, 0, trail, h);
  }
  // The phrase being played
  if (curSeg) {
    const a = x(Number(curSeg.start) || 0) - 2;
    const b = x(Number(curSeg.end) || Number(curSeg.start) || 0) + 2;
    rpRoundRect(ctx, a, 20, Math.max(5, b - a), h - 25, 6);
    ctx.fillStyle = rpHexA(curColor, 0.08);
    ctx.fill();
    ctx.strokeStyle = rpHexA(curColor, 0.33);
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  // A–B repeat
  if (rp.ab) {
    const a = x(rp.ab.a), b = x(rp.ab.b == null ? rp.ab.a : rp.ab.b);
    rpRoundRect(ctx, a, 2, Math.max(3, b - a), h - 4, 6);
    ctx.fillStyle = 'rgba(255,179,64,0.07)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,179,64,0.75)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  // The pauses that are skipped: hatched
  if (state.skipSilence && rp.pauses.length) {
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.10)';
    ctx.lineWidth = 1;
    for (const [a0, b0] of rp.pauses) {
      const a = x(a0), bw = Math.max(3, x(b0) - a);
      ctx.save();
      ctx.beginPath();
      ctx.rect(a, 18, bw, h - 36);
      ctx.clip();
      for (let k = a - h; k < a + bw; k += 4) { ctx.beginPath(); ctx.moveTo(k, h - 18); ctx.lineTo(k + h, 18 - h + 36); ctx.stroke(); }
      ctx.restore();
    }
    ctx.restore();
  }

  // The bars: above a line at 58 % of their band, a faint reflection below it
  const peaks = rp.peaks.length ? rp.peaks : new Array(RP_BARS).fill(0.14);
  const n = peaks.length;
  const gap = 2;
  const barW = Math.max(1.5, (w - gap * (n - 1)) / n);
  const bandTop = 24, bandH = Math.max(20, h - 32);
  const base = bandTop + bandH * 0.58;
  const pos = ratio * n;
  const filter = state.speakerFilter;
  for (let i = 0; i < n; i++) {
    const bx = i * (barW + gap);
    const t = ((i + 0.5) / n) * dur;
    const who = rp.barSpk[i];
    const off = !!filter && who && who !== filter;
    const hush = state.skipSilence && rp.pauses.some(([a, b]) => t >= a && t <= b);
    const k = off ? 0.35 : hush ? 0.55 : 1;
    const amp = Math.max(0.1, peaks[i]);
    const topH = Math.max(2, amp * bandH * 0.52 * k);
    const botH = Math.max(1.5, amp * bandH * 0.19 * k);
    const color = off ? '#3A3A3F' : rpSpeaker(who).color;
    const frac = Math.max(0, Math.min(1, pos - i));
    const ahead = off ? '#2C2C30' : rpHexA(color, 0.42);
    let fill = frac >= 1 ? color : frac <= 0 ? ahead : null;
    if (!fill) {
      const g = ctx.createLinearGradient(bx, 0, bx + barW, 0);
      g.addColorStop(0, color); g.addColorStop(frac, color); g.addColorStop(frac, ahead); g.addColorStop(1, ahead);
      fill = g;
    }
    if (state.playerHoverRatio >= 0 && frac <= 0 && i / n <= state.playerHoverRatio && !off) fill = rpHexA(color, 0.62);
    ctx.globalAlpha = off ? 0.7 : 1;
    ctx.fillStyle = fill;
    rpRoundRect(ctx, bx, base - topH, barW, topH, Math.min(3, barW / 2));
    ctx.fill();
    ctx.globalAlpha = off ? 0.2 : frac > 0 ? 0.45 : 0.5;
    rpRoundRect(ctx, bx, base + 1.5, barW, botH, Math.min(3, barW / 2));
    ctx.fill();
  }
  ctx.globalAlpha = 1;

  // Who speaks when: a ribbon at the bottom
  segs.forEach((s, i) => {
    const a = x(Number(s.start) || 0), b = x(Number(s.end) || Number(s.start) || 0);
    if (b - a < 0.5) return;
    const col = rpSpeaker(s.speaker).color;
    const on = i === idx;
    ctx.globalAlpha = filter && s.speaker !== filter ? 0.15 : on ? 1 : (Number(s.end) || 0) <= cur ? 0.75 : 0.4;
    ctx.fillStyle = col;
    rpRoundRect(ctx, a, h - (on ? 11 : 10), Math.max(1, b - a - 1), on ? 6 : 4, 2);
    ctx.fill();
  });
  ctx.globalAlpha = 1;

  // The marked moments
  for (const t of rp.marks) {
    const mx = x(t);
    ctx.fillStyle = 'rgba(255,214,10,0.6)';
    ctx.fillRect(mx - 1, 0, 2, h);
    ctx.save();
    ctx.translate(mx, 7);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = '#FFD60A';
    ctx.fillRect(-4, -4, 8, 8);
    ctx.restore();
  }
  // The mouse and the playhead
  if (state.playerHoverRatio >= 0 && !rp.drag) {
    ctx.fillStyle = 'rgba(245,245,247,0.45)';
    ctx.fillRect(state.playerHoverRatio * w, 0, 1, h);
  }
  ctx.shadowColor = 'rgba(255,255,255,0.7)';
  ctx.shadowBlur = 6;
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(ratio * w - 1, 0, 2, h);
  ctx.shadowBlur = 0;

  rpPlaceBubbles(ratio, cur, curSeg, curColor);
  rpRenderNow(cur, curSeg, curColor, idx);
}

function rpRoundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, Math.max(0, Math.min(r, w / 2, h / 2)));
  else ctx.rect(x, y, w, h);
}

function rpChapters() {
  return (typeof _chapters !== 'undefined' && Array.isArray(_chapters)) ? _chapters : [];
}

// The chapter titles over their bands (buttons: a click plays the chapter)
function rpRenderChapters() {
  const box = document.getElementById('rp-chapters');
  if (!box) return;
  const dur = rpDuration();
  const list = rpChapters();
  if (!dur || list.length < 2) { box.innerHTML = ''; return; }
  box.innerHTML = list.map((c, i) => {
    const a = (c.start / dur) * 100;
    const b = i + 1 < list.length ? (list[i + 1].start / dur) * 100 : 100;
    return `<button class="rp-chapter" data-rp-chapter="${i}" data-click="seekPlayer" data-click-args='[${Number(c.start) || 0}]'
      style="left: calc(${a}% + 8px); max-width: calc(${b - a}% - 16px)" title="${escapeHtml(c.desc || c.title)}">${escapeHtml(c.title)}</button>`;
  }).join('');
}

function rpPlaceBubbles(ratio, cur, seg, color) {
  const head = document.getElementById('rp-head-bubble');
  const knob = document.getElementById('rp-knob');
  const hov = document.getElementById('rp-hover-bubble');
  const wrap = document.getElementById('player-waveform-wrapper');
  if (!head || !knob || !hov || !wrap) return;
  const left = `${ratio * 100}%`;
  head.style.left = left;
  knob.style.left = left;
  head.style.background = color;
  const name = seg ? rpSpeaker(seg.speaker).name : '';
  head.textContent = rp.drag && name ? `${formatTime(cur)} · ${name}` : formatTime(cur);
  knob.style.boxShadow = `0 0 0 2px #17181B, 0 0 10px ${rpHexA(color, 0.6)}`;
  wrap.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
  wrap.setAttribute('aria-valuetext', formatTime(cur));
  // The mouse bubble: its time and who speaks there; beside the playhead bubble when they meet
  if (state.playerHoverRatio < 0 || rp.drag) { hov.classList.add('hidden'); return; }
  const dur = rpDuration();
  const t = state.playerHoverRatio * dur;
  const si = rpSegIndexAt(t);
  const who = si >= 0 ? rpSpeaker(rpSegments()[si].speaker).name : '';
  hov.textContent = who ? `${formatTime(t)} · ${who}` : formatTime(t);
  hov.classList.remove('hidden');
  const width = wrap.getBoundingClientRect().width / ((typeof uiZoom === 'function' ? uiZoom() : 1) || 1);
  const dx = (state.playerHoverRatio - ratio) * width;
  hov.style.left = `${state.playerHoverRatio * 100}%`;
  hov.style.transform = Math.abs(dx) > 70 ? 'translateX(-50%)' : dx >= 0 ? 'translateX(36px)' : 'translateX(calc(-100% - 36px))';
}

// Who speaks now, and the words of the phrase: the said ones bright, the next ones dim
function rpRenderNow(cur, seg, color, idx) {
  const btn = document.getElementById('rp-speaker');
  if (!btn) return;
  btn.classList.toggle('hidden', !seg);
  if (!seg) return;
  const spk = rpSpeaker(seg.speaker);
  document.getElementById('rp-speaker-dot').style.background = color;
  document.getElementById('rp-speaker-dot').style.boxShadow = `0 0 0 3px ${rpHexA(color, 0.22)}`;
  const nameEl = document.getElementById('rp-speaker-name');
  nameEl.textContent = spk.name;
  nameEl.style.color = color;
  const cap = document.getElementById('rp-caption');
  const words = (seg.words || []).filter(w => String(w.word || '').trim());
  const key = `${idx}:${words.length ? words.findIndex(w => (Number(w.start) || 0) > cur) : 0}:${state.noFillers ? 1 : 0}`;
  if (cap.dataset.key === key) return;
  cap.dataset.key = key;
  const hide = new Set(state.noFillers && rp.fillers ? ((rp.fillers.segments || {})[String(seg.id)] || {}).drop || [] : []);
  if (!words.length) { cap.textContent = rpShownText(seg); return; }
  const all = (seg.words || []).map((w, i) => ({ w, i })).filter(({ w, i }) => String(w.word || '').trim() && !hide.has(i));
  let k = all.findIndex(({ w }) => (Number(w.start) || 0) > cur);
  if (k < 0) k = all.length;
  const from = Math.max(0, k - 8);
  cap.innerHTML = all.slice(from, k + 4).map(({ w }, j) =>
    `<span class="${from + j < k ? 'rp-w-past' : 'rp-w-next'}">${escapeHtml(w.word)}</span>`).join('');
}

// The text of a phrase as it is shown (without fillers when «Без запинок» is on)
function rpShownText(seg) {
  if (state.noFillers && rp.fillers) {
    const v = (rp.fillers.segments || {})[String(seg.id)];
    if (v && typeof v.text === 'string') return v.text;
  }
  return seg.text || '';
}

// --- While playing: the playhead moves smoothly; A–B, the phrase loop and the pauses are kept ---
function rpTick() {
  rp.raf = 0;
  const p = rpPlayer();
  if (!p || p.paused) { rpRender(); return; }
  const now = p.currentTime || 0;
  if (rp.ab && rp.ab.b != null && now >= rp.ab.b) p.currentTime = rp.ab.a;
  else if (rp.loopSeg) {
    const s = rp.loopSeg;
    if (now >= (Number(s.end) || 0) - 0.05 || now < (Number(s.start) || 0) - 0.5) p.currentTime = Number(s.start) || 0;
  } else if (state.skipSilence && rp.pauses.length) {
    const gap = rp.pauses.find(([a, b]) => now >= a && now < b - 0.05);
    if (gap) {
      p.currentTime = gap[1];
      rp.skipped += gap[1] - now;
      rpShowSkipNote(t('rdPlyrSkipped').replace('{s}', () => Math.max(1, Math.round(gap[1] - now))));
    }
  }
  rpRender();
  rpRing(p);
  rp.raf = requestAnimationFrame(rpTick);
}

function rpStartTicking() {
  if (!rp.raf) rp.raf = requestAnimationFrame(rpTick);
}

function rpShowSkipNote(text) {
  const el = document.getElementById('rp-skip-note');
  if (!el || !text) return;
  el.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4 5l9 7-9 7zM13 5l9 7-9 7z"/></svg>${escapeHtml(text)}`;
  const dur = rpDuration() || 1;
  const p = rpPlayer();
  el.style.left = `min(calc(${(((p && p.currentTime) || 0) / dur) * 100}% + 10px), calc(100% - 190px))`;
  el.classList.remove('hidden');
  clearTimeout(rp.skipTimer);
  rp.skipTimer = setTimeout(() => el.classList.add('hidden'), 1800);
  if (rp.open === 'sound') rpRenderPop();
}

// The ring around the play button: how much of the phrase is played
function rpRing(p) {
  const ring = document.getElementById('rp-ring-fill');
  if (!ring) return;
  const segs = rpSegments();
  const t = (p && p.currentTime) || 0;
  const i = rpSegIndexAt(t);
  let frac = 0, color = '#0A84FF';
  if (i >= 0) {
    const s = segs[i];
    const end = segs[i + 1] ? Number(segs[i + 1].start) : (Number(s.end) || rpDuration());
    frac = Math.max(0, Math.min(1, (t - (Number(s.start) || 0)) / Math.max(0.5, end - (Number(s.start) || 0))));
    color = rpSpeaker(s.speaker).color;
  }
  ring.setAttribute('stroke-dasharray', `${(frac * 175.9).toFixed(1)} 176`);
  ring.style.stroke = color;
}

// --- The wave: click, drag, double click, keys ---
function rpInitWave() {
  const wrap = document.getElementById('player-waveform-wrapper');
  if (!wrap) return;
  const ratioAt = (evt) => {
    const r = wrap.getBoundingClientRect();
    return Math.max(0, Math.min(1, (evt.clientX - r.left) / Math.max(1, r.width)));
  };
  const seekTo = (ratio) => {
    const p = rpPlayer();
    const dur = rpDuration();
    if (p && dur > 0) p.currentTime = ratio * dur;
    rpRender();
  };
  wrap.addEventListener('pointerdown', (evt) => {
    if (evt.button !== 0 || evt.target.closest('.rp-chapter')) return;
    rp.drag = true;
    wrap.setPointerCapture(evt.pointerId);
    // Grabbing the time bubble or the knob keeps the spot under the pointer: no jump on the press,
    // and the drag moves the time by as much as the pointer moves
    rp.grabOffset = 0;
    if (evt.target.closest('.rp-bubble, .rp-knob')) {
      const p = rpPlayer(), dur = rpDuration();
      if (p && dur > 0) { rp.grabOffset = p.currentTime / dur - ratioAt(evt); return; }
    }
    seekTo(ratioAt(evt));
  });
  wrap.addEventListener('pointermove', (evt) => {
    if (rp.drag) seekTo(Math.max(0, Math.min(1, ratioAt(evt) + (rp.grabOffset || 0))));
    else { state.playerHoverRatio = ratioAt(evt); rpRender(); }
  });
  const end = () => { if (rp.drag) { rp.drag = false; rpRender(); } };
  wrap.addEventListener('pointerup', end);
  wrap.addEventListener('pointercancel', end);
  wrap.addEventListener('pointerleave', () => { state.playerHoverRatio = -1; rpRender(); });
  wrap.addEventListener('dblclick', (evt) => {
    const dur = rpDuration();
    if (!dur) return;
    rpJumpToText(ratioAt(evt) * dur);
  });
  window.addEventListener('resize', () => { rpRender(); });
}

function rpScrubKey(e) {
  const p = rpPlayer();
  if (!p) return;
  const dur = rpDuration();
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault();
    e.stopPropagation();
    p.currentTime = Math.max(0, Math.min(dur, (p.currentTime || 0) + (e.key === 'ArrowLeft' ? -5 : 5) * (e.shiftKey ? 3 : 1)));
  } else if (e.key === 'Home' || e.key === 'End') {
    e.preventDefault();
    p.currentTime = e.key === 'Home' ? 0 : Math.max(0, dur - 0.5);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    rpJumpToText(p.currentTime || 0);
  } else return;
  rpRender();
}

// The text at this moment: the transcript scrolls to the phrase and marks it for a moment
function rpJumpToText(t) {
  const p = rpPlayer();
  if (p) p.currentTime = t;
  if (state.activeWorkspaceTab !== 'transcript' && !state.theatre && typeof openRecordTab === 'function') openRecordTab('transcript');
  const segs = rpSegments();
  const i = rpSegIndexAt(t);
  const seg = segs[Math.max(0, i)];
  if (!seg) return;
  setTimeout(() => {
    if (typeof scrollToSegment === 'function') scrollToSegment(seg.id);
    const el = document.getElementById(`seg-${seg.id}`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('rp-jump');
    void el.offsetWidth;
    el.classList.add('rp-jump');
    setTimeout(() => el.classList.remove('rp-jump'), 1500);
  }, 60);
  rpRender();
}

function rpShowPhrase() {
  const p = rpPlayer();
  rpJumpToText((p && p.currentTime) || 0);
}

// --- The transport ---
function rpRepeatPhrase() {
  const p = rpPlayer();
  if (!p) return;
  const seg = rpSegments()[rpSegIndexAt(p.currentTime || 0)];
  seekPlayer(seg ? Number(seg.start) || 0 : 0);
}

// The previous / next phrase (Shift: the chapter). Back goes to the start of this phrase first
function rpStepPhrase(dir, e) {
  const p = rpPlayer();
  if (!p) return;
  const t = p.currentTime || 0;
  const starts = (e && e.shiftKey && rpChapters().length) ? rpChapters().map(c => Number(c.start) || 0)
    : rpSegments().map(s => Number(s.start) || 0);
  if (!starts.length) { skipPlayer(dir * 15); return; }
  let target;
  if (dir > 0) target = starts.find(s => s > t + 0.2);
  else {
    const before = starts.filter(s => s < t - 1.5);
    target = before.length ? before[before.length - 1] : 0;
  }
  if (target == null) return;
  p.currentTime = target;
  rpRender();
}

function rpToggleLoop() {
  const p = rpPlayer();
  if (rp.loopSeg) rp.loopSeg = null;
  else {
    const seg = rpSegments()[rpSegIndexAt((p && p.currentTime) || 0)];
    if (!seg) return;
    rp.loopSeg = seg;
    rp.ab = null;
  }
  rpUpdateLoopButton();
  rpRender();
}

function rpUpdateLoopButton() {
  const b = document.getElementById('rp-loop-btn');
  if (b) b.setAttribute('aria-pressed', String(!!rp.loopSeg));
}

function rpToggleRemain() {
  rp.remain = !rp.remain;
  rpUpdateEndTime();
}

function rpUpdateEndTime() {
  const el = document.getElementById('player-total-time');
  if (!el) return;
  const dur = rpDuration();
  const p = rpPlayer();
  el.textContent = rp.remain ? `−${formatTime(Math.max(0, dur - ((p && p.currentTime) || 0)))}` : formatTime(dur);
  el.title = t(rp.remain ? 'rdPlyrTipTotal' : 'rdPlyrTipRemain');
}

// --- A–B repeat and marked moments (⋯ menu, keys A, B, M) ---
function rpSetA() {
  const p = rpPlayer();
  rp.ab = { a: Math.floor((p && p.currentTime) || 0), b: null };
  rp.loopSeg = null;
  rpUpdateLoopButton();
  rpClosePop();
  rpRender();
}

function rpSetB() {
  const p = rpPlayer();
  if (!rp.ab) { rpSetA(); return; }
  const b = Math.floor((p && p.currentTime) || 0);
  if (b <= rp.ab.a + 1) { showToast(t('rdPlyrAbTooShort')); return; }
  rp.ab = { a: rp.ab.a, b };
  if (p) p.currentTime = rp.ab.a;
  rpClosePop();
  rpRender();
}

function rpClearAb() {
  rp.ab = null;
  rpClosePop();
  rpRender();
}

function rpLoadMarks(id) {
  if (!id) return [];
  try { return JSON.parse(localStorage.getItem(`verbascribe_marks_${id}`) || '[]').filter(Number.isFinite); } catch (e) { return []; }
}

function rpSaveMarks() {
  if (!rp.meetingId) return;
  try { localStorage.setItem(`verbascribe_marks_${rp.meetingId}`, JSON.stringify(rp.marks)); } catch (e) {}
}

function rpMarkMoment() {
  const p = rpPlayer();
  const now = (p && p.currentTime) || 0;
  rp.marks = rp.marks.filter(x => Math.abs(x - now) > 1).concat(Math.round(now * 10) / 10).sort((a, b) => a - b);
  rpSaveMarks();
  rpClosePop();
  rpRender();
  showToast(t('rdPlyrMarked').replace('{t}', () => formatTime(now)));
}

function rpClearMarks() {
  rp.marks = [];
  rpSaveMarks();
  rpClosePop();
  rpRender();
}

function rpNextMark() {
  const p = rpPlayer();
  const t = (p && p.currentTime) || 0;
  const next = rp.marks.find(x => x > t + 0.5);
  if (next != null && p) p.currentTime = next;
}

// --- Popovers: one open at a time; a click outside closes it, Esc too ---
function rpTogglePop(name) {
  rp.open = rp.open === name ? null : name;
  rp.saveAudioOpen = false;
  rpRenderPop();
}

function rpClosePop() {
  if (!rp.open) return;
  rp.open = null;
  rpRenderPop();
}

function rpRenderPop() {
  ['sound', 'speed', 'more'].forEach(k => {
    const pop = document.getElementById(`rp-pop-${k}`);
    const btn = document.getElementById({ sound: 'rp-sound-btn', speed: 'speed-cycle-btn', more: 'rp-more-btn' }[k]);
    const on = rp.open === k;
    if (btn) btn.setAttribute('aria-expanded', String(on));
    if (!pop) return;
    pop.classList.toggle('hidden', !on);
    if (on) pop.innerHTML = { sound: rpSoundHtml, speed: rpSpeedHtml, more: rpMoreHtml }[k]();
  });
}

function rpItem(label, fn, args, opts = {}) {
  return `<button class="rp-item${opts.danger ? ' rp-item-danger' : ''}" role="${opts.role || 'menuitem'}"${opts.checked != null ? ` aria-checked="${opts.checked}"` : ''}
    data-click="${fn}" data-click-args='${jsArg(args || [])}'${opts.disabled ? ' disabled' : ''}${opts.title ? ` title="${escapeHtml(opts.title)}"` : ''}>
    <span class="rp-item-label">${escapeHtml(label)}</span>${opts.mark ? `<span class="rp-item-mark">${escapeHtml(opts.mark)}</span>` : ''}${opts.key ? `<span class="rp-item-key">${escapeHtml(opts.key)}</span>` : ''}</button>`;
}

function rpSwitch(label, hint, on, fn) {
  return `<button class="rp-switch-row" role="switch" aria-checked="${on}" data-click="${fn}">
    <span class="rp-switch-text"><span class="rp-switch-label">${escapeHtml(label)}</span><span class="rp-switch-hint">${escapeHtml(hint)}</span></span>
    <span class="vs-track" aria-hidden="true"></span></button>`;
}

// «Звук»: what is done to the sound while listening; the file stays as it is
function rpSoundHtml() {
  const m = state.activeMeeting;
  const dubs = m ? availableDubs(m) : [];
  const n = rp.pauses.length;
  const pausesHint = n
    ? t('rdPlyrSkipHintN').replace('{n}', () => n).replace('{t}', () => formatTime(rp.pauseSec))
      + (rp.skipped >= 1 ? t('rdPlyrSkipHintDone').replace('{s}', () => Math.round(rp.skipped)) : '')
    : t('rdPlyrSkipHint');
  let html = `<div class="rp-pop-title">${escapeHtml(t('rdPlyrWhileListening'))}</div>`;
  html += rpSwitch(t('rdPlyrStudioVoice'), t('rdPlyrStudioVoiceHint'), !!state.studioVoiceActive, 'rpToggleStudioVoice');
  html += rpSwitch(t('rdPlyrSkipSilence'), pausesHint, !!state.skipSilence, 'rpToggleSkipSilence');
  if (dubs.length) {
    const opt = (key, label) => `<button role="radio" aria-checked="${state.audioTrack === key}" data-click="rpSetTrack" data-click-args='${jsArg([key])}'>${escapeHtml(label)}</button>`;
    html += `<div class="rp-note-row" style="align-items:center"><span class="rp-switch-label" style="flex:1">${escapeHtml(t('rdPlyrTrack'))}</span>
      <div class="rp-seg" role="radiogroup" aria-label="${escapeHtml(t('rdPlyrTrack'))}">${opt('orig', t('trackOriginal'))}${dubs.map(d => opt(d.lang, `${t('trackDub')} ${d.lang.toUpperCase()}`)).join('')}</div></div>`;
  }
  html += `<div class="rp-divider"></div><div class="rp-note-row"><span class="rp-note">${escapeHtml(t('rdPlyrSoundNote'))}</span>
    <button class="rp-small-btn" data-click="rpOpenInfo">${escapeHtml(t('rdPlyrDetailsBtn'))}</button></div>`;
  return html;
}

function rpToggleStudioVoice() {
  toggleStudioVoiceEnhancer(true);
  rpRenderSoundChip();
  rpRenderPop();
}

function rpToggleSkipSilence() {
  state.skipSilence = !state.skipSilence;
  try { localStorage.setItem('verbascribe_skip_silence', state.skipSilence ? '1' : '0'); } catch (e) {}
  rpRenderSoundChip();
  rpRenderPop();
  rpRender();
}

function rpSetTrack(key) {
  setAudioTrack(key);
  rpRenderSoundChip();
  rpRenderPop();
}

function rpRenderSoundChip() {
  const btn = document.getElementById('rp-sound-btn');
  const badge = document.getElementById('rp-sound-n');
  if (!btn || !badge) return;
  const n = (state.studioVoiceActive ? 1 : 0) + (state.skipSilence ? 1 : 0) + (state.audioTrack && state.audioTrack !== 'orig' ? 1 : 0);
  badge.textContent = String(n);
  badge.classList.toggle('hidden', !n);
  btn.classList.toggle('rp-chip-on', n > 0);
}

// Speed: a slider from 0.25× to 3×, − and +, the usual values as chips
function rpSpeedLabel(r) {
  const v = Math.round(r * 100) / 100;
  return `${String(v).replace('.', state.uiLang === 'en' ? '.' : ',')}×`;
}

function rpSpeedHtml() {
  const r = rpPlayer() ? rpPlayer().playbackRate || 1 : 1;
  return `<div class="rp-speed-head"><span class="rp-speed-title">${escapeHtml(t('rdPlyrSpeed'))}</span><span class="rp-speed-val">${escapeHtml(rpSpeedLabel(r))}</span></div>
    <div class="rp-speed-line">
      <button class="rp-speed-step" data-click="rpSpeedStep" data-click-args='[-0.05]' aria-label="${escapeHtml(t('rdPlyrSlower'))}" title="${escapeHtml(t('rdPlyrSlower'))}">−</button>
      <input class="rp-speed-range" type="range" min="0.25" max="3" step="0.05" value="${r}" data-input="rpSetSpeed" data-input-args='[{"$": "value"}]' aria-label="${escapeHtml(t('rdPlyrSpeed'))}">
      <button class="rp-speed-step" data-click="rpSpeedStep" data-click-args='[0.05]' aria-label="${escapeHtml(t('rdPlyrFaster'))}" title="${escapeHtml(t('rdPlyrFaster'))}">+</button>
    </div>
    <div class="rp-speed-chips">${RP_SPEEDS.map(v => `<button aria-pressed="${Math.abs(v - r) < 0.01}" data-click="rpSetSpeed" data-click-args='[${v}]'>${escapeHtml(rpSpeedLabel(v))}</button>`).join('')}</div>`;
}

function rpSetSpeed(v, keepOpen) {
  const r = Math.max(0.25, Math.min(3, Math.round(Number(v) * 100) / 100 || 1));
  const p = rpPlayer();
  if (p) p.playbackRate = r;
  state.playbackRate = r;
  const i = state.playbackSpeeds.findIndex(x => Math.abs(x - r) < 0.01);
  if (i >= 0) state.speedIndex = i;
  const btn = document.getElementById('speed-cycle-btn');
  if (btn) btn.textContent = rpSpeedLabel(r);
  btn?.classList.toggle('rp-chip-on', Math.abs(r - 1) > 0.01);
  // The slider keeps its place while it is dragged; the rest of the popover follows
  const pop = document.getElementById('rp-pop-speed');
  if (pop && rp.open === 'speed') {
    pop.querySelector('.rp-speed-val').textContent = rpSpeedLabel(r);
    pop.querySelectorAll('.rp-speed-chips button').forEach((b, k) => b.setAttribute('aria-pressed', String(Math.abs(RP_SPEEDS[k] - r) < 0.01)));
    const range = pop.querySelector('.rp-speed-range');
    if (range && document.activeElement !== range) range.value = r;
  }
}

function rpSpeedStep(d) {
  const p = rpPlayer();
  rpSetSpeed(((p && p.playbackRate) || 1) + d);
}

// ⋯: A–B, marks, saving the sound
function rpMoreHtml() {
  const m = state.activeMeeting;
  const p = rpPlayer();
  const hasMedia = !!(m && !m.media_removed && p && p.src);
  const done = !!(m && (m.segments || []).length);
  let html = '';
  if (!rp.ab) html += rpItem(t('rdPlyrAbStart'), 'rpSetA', [], { key: 'A', disabled: !hasMedia });
  else if (rp.ab.b == null) html += rpItem(t('rdPlyrAbEnd'), 'rpSetB', [], { key: `B · ${formatTime(rp.ab.a)}` });
  else html += rpItem(t('rdPlyrAbClear'), 'rpClearAb', [], { mark: '✓', key: `${formatTime(rp.ab.a)}–${formatTime(rp.ab.b)}` });
  html += rpItem(t('rdPlyrMark'), 'rpMarkMoment', [], { key: rp.marks.length ? `M · ${rp.marks.length}` : 'M', disabled: !hasMedia });
  if (rp.marks.length) html += rpItem(t('rdPlyrNextMark'), 'rpNextMark', [], { key: 'Shift+M' }) + rpItem(t('rdPlyrClearMarks'), 'rpClearMarks', []);
  html += '<div class="rp-divider"></div>';
  html += rpItem(t('rdPlyrSaveAudio'), 'rpToggleSaveAudio', [], { key: 'MP3 · WAV', disabled: !hasMedia });
  if (rp.saveAudioOpen) {
    html += `<div class="rp-note-row" style="padding-left:22px"><button class="rp-small-btn" data-click="rpSaveAudio" data-click-args='["mp3"]'>MP3</button>
      <button class="rp-small-btn" data-click="rpSaveAudio" data-click-args='["wav"]'>WAV</button><span class="rp-note">${escapeHtml(t('rdPlyrSaveAudioNote'))}</span></div>`;
  }
  html += rpItem(t('rdPlyrSaveClean'), 'rpSaveClean', [], { key: t('rdPlyrNewFile'), disabled: !hasMedia || !done, title: done ? t('tipCutClean') : t('exportCutNeedsText') });
  for (const d of (m ? availableDubs(m) : [])) {
    html += rpItem(t('rdPlyrSaveDub').replace('{lang}', () => d.lang.toUpperCase()), 'saveDubFile', [d.lang, 'media'], {});
  }
  return html;
}

function rpToggleSaveAudio() {
  rp.saveAudioOpen = !rp.saveAudioOpen;
  rpRenderPop();
}

async function rpSaveAudio(fmt) {
  const m = state.activeMeeting;
  if (!m) return;
  rpClosePop();
  showToast(t('rdPlyrSavingAudio'));
  const res = await fetch(`/api/meetings/${m.id}/save-audio`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fmt }),
  });
  const data = await readJson(res).catch(() => ({}));
  if (!res.ok) { showToast(errorText(data, res)); return; }
  if (data.cancelled) return;
  showToast(`${t('exportSavedTo')} ${data.filename}`, { label: t('showInFolder'), title: t('tipShowInFolder'), onClick: () => revealPath(data.path) });
}

function rpSaveClean() {
  rpClosePop();
  requestCut('clean');
}

// The video's own buttons sit on the black panel beside it (static/parts/workspace.html #video-tools):
// smaller, theatre, a window over the other programs; the small one opens back with a click
function rpRenderVideoButton() {
  document.getElementById('video-tool-window')?.classList.toggle('hidden', !document.pictureInPictureEnabled);
  rpFitRight();
}

// The chips on the right keep only their icons (their tooltips name them) and the volume gets
// shorter when the row would run into the transport buttons in the middle
let rpRightObserver = null;
function rpFitRight() {
  const right = document.querySelector('.rp-right');
  if (!right) return;
  if (!rpRightObserver && window.ResizeObserver) {
    rpRightObserver = new ResizeObserver(() => rpFitRight());
    rpRightObserver.observe(right.parentElement);
  }
  // flex-end: what does not fit runs out on the left, where scrollWidth does not count it
  const over = () => {
    const kids = [...right.children].filter(el => el.offsetWidth);
    const gap = parseFloat(getComputedStyle(right).columnGap) || 0;
    return kids.reduce((n, el) => n + el.offsetWidth, 0) + gap * Math.max(0, kids.length - 1) > right.clientWidth + 1;
  };
  right.classList.remove('rp-tight', 'rp-crowded');
  if (over()) right.classList.add('rp-tight');
  if (over()) right.classList.add('rp-crowded');
}

async function rpSetVideo(k) {
  rpClosePop();
  const p = rpPlayer();
  if (document.pictureInPictureElement === p && k !== 'window') await document.exitPictureInPicture().catch(() => {});
  if (k === 'theatre') { if (!state.theatre) toggleTheatre(); }
  else {
    if (state.theatre) exitTheatre();
    if (k === 'window') toggleVideoPip();
    else setVideoMode(k === 'mini' ? 'mini' : 'expanded');
  }
  rpRenderVideoButton();
}

// --- «Без запинок»: the text is shown without fillers; the transcript itself is not changed ---
function rpToggleNoFillers() {
  state.noFillers = !state.noFillers;
  rpRenderFillerButton();
  if (typeof renderTranscript === 'function') renderTranscript();
  rpRender();
}

function rpRenderFillerButton() {
  const btn = document.getElementById('clean-fillers-btn');
  if (!btn) return;
  const on = !!state.noFillers;
  btn.setAttribute('aria-pressed', String(on));
  btn.classList.toggle('ui-btn-active', on);
  btn.classList.toggle('ui-btn-subtle', !on);
  const label = btn.querySelector('.tx-tool-label');
  const f = rp.fillers;
  if (label) label.textContent = on && f && f.count ? `${t('rdPlyrNoFillers')} · ${t('rdPlyrHiddenN').replace('{n}', () => f.count)}` : t('rdPlyrNoFillers');
  btn.title = t('rdPlyrNoFillersTip');
  const m = state.activeMeeting;
  document.getElementById('rp-restore-fillers')?.classList.toggle('hidden', !(m && m.has_segments_backup));
}

// The text of a recording cleaned the old way (before «Без запинок» only hid them): brought back
async function rpRestoreFillers() {
  const m = state.activeMeeting;
  if (!m || !m.has_segments_backup) return;
  await restoreSegments(m.id);
  rpRenderFillerButton();
}

// The words a phrase hides when «Без запинок» is on (word indices)
function rpHiddenWords(seg) {
  if (!state.noFillers || !rp.fillers) return null;
  const v = (rp.fillers.segments || {})[String(seg.id)];
  return v ? v : null;
}

// --- The options of the text export (the «Экспорт» menu): the exported file only ---
function rpExportOptions() {
  const o = state.exportOpts || {};
  // «Без запинок» is off until ticked: an export keeps every word unless asked (and JSON always does)
  return { times: o.times !== false, names: o.names !== false, nofill: !!o.nofill };
}

function rpExportOptionsHtml() {
  const o = rpExportOptions();
  const box = (key, label, hint) => `<label class="rp-xo" title="${escapeHtml(hint)}" data-click="stopEvent" data-click-args='[{"$": "event"}]'>
    <input type="checkbox" ${o[key] ? 'checked' : ''} data-change="rpSetExportOpt" data-change-args='${jsArg([key, { $: 'checked' }])}'>${escapeHtml(label)}</label>`;
  return `<div class="rp-xo-row">${box('times', t('rdPlyrXoTimes'), t('rdPlyrXoTimesTip'))}${box('names', t('rdPlyrXoNames'), t('rdPlyrXoNamesTip'))}${box('nofill', t('rdPlyrNoFillers'), t('rdPlyrXoNoFillTip'))}</div>`;
}

function rpSetExportOpt(key, on) {
  state.exportOpts = { ...rpExportOptions(), [key]: !!on };
}

// --- «Сведения о записи» (Ctrl+I, the ⋯ of the record): the file, dates, format, recognition,
// what the sound went through, how it sounds now, and the copies saved from it ---
async function rpOpenInfo() {
  rpClosePop();
  const m = state.activeMeeting;
  if (!m) return;
  const box = document.getElementById('recinfo-body');
  if (!box) return;
  box.innerHTML = `<div class="py-10 text-center text-xs text-apple-secondary animate-pulse">…</div>`;
  openModal('recinfo');
  const id = m.id;
  try {
    const [infoRes, copiesRes] = await Promise.all([fetch(`/api/meetings/${id}/info`), fetch(`/api/meetings/${id}/copies`)]);
    const info = await readJson(infoRes);
    if (!infoRes.ok) throw new Error(errorText(info, infoRes));
    const copies = copiesRes.ok ? await readJson(copiesRes) : [];
    if (!state.activeMeeting || state.activeMeeting.id !== id) return;
    rp.info = info;
    rp.copies = copies;
    rpRenderInfo();
  } catch (e) {
    box.innerHTML = `<div class="py-6 text-xs text-apple-red">${escapeHtml(e.message)}</div>`;
  }
}

function rpRenderInfo() {
  const box = document.getElementById('recinfo-body');
  const info = rp.info;
  const m = state.activeMeeting;
  if (!box || !info || !m) return;
  const media = info.media || {};
  const probe = info.probe || {};
  const v = probe.video, a = probe.audio;
  const row = (k, val, opts = {}) => (val == null || val === '') ? '' :
    `<div class="ri-row"><span class="ri-k">${escapeHtml(k)}</span><span class="ri-v ${opts.cls || ''}" title="${escapeHtml(opts.full || String(val))}"${opts.mono ? ' style="font-family: var(--font-mono); font-size: 12px"' : ''}>${opts.html ? val : escapeHtml(String(val))}</span></div>`;
  const yes = (k, val) => row(k, `✓ ${val}`, { cls: 'ri-yes' });
  const no = (k, val) => row(k, `— ${val}`, { cls: 'ri-no' });
  const flag = (k, on, onText, offText) => (on ? yes : no)(k, on ? onText : offText);
  const btn = (label, fn, args) => `<button class="rp-small-btn" data-click="${fn}" data-click-args='${jsArg(args || [])}'>${escapeHtml(label)}</button>`;
  const card = (title, rows, actions = '', note = '') => `<section class="ri-card"><div class="ri-card-head"><h3 class="ri-card-title">${escapeHtml(title)}</h3>${actions}</div>${rows}${note ? `<p class="ri-note">${escapeHtml(note)}</p>` : ''}</section>`;
  const folderPath = (info.folders || []).map(f => f.system_key ? t(`folderSystem_${f.system_key}`) : f.name).join(' / ');

  const fileCard = card(t('infoFile'),
    row(t('infoName'), media.name || info.filename)
    + row(t('infoLocation'), media.path ? String(media.path).replace(/[\\/][^\\/]*$/, '') : '', { full: media.path })
    + (media.exists === false ? row(t('infoState'), t('infoMissing'), { cls: 'text-apple-red' }) : '')
    + row(t('infoSize'), media.size != null ? formatBytes(media.size) : '')
    + row(t('infoSourceLabel'), t(`infoSource_${info.source || 'import'}`) + (info.owns_media ? '' : ` · ${t('infoInPlace')}`))
    + row(t('infoFolder'), folderPath || t('infoNoFolder')),
    media.exists ? btn(t('showInFolder'), 'revealPath', [media.path]) + btn(t('copyPath'), 'copyText', [media.path]) : '');
  const datesCard = card(t('infoDates'),
    row(t('infoRecordedAt'), formatDateTime(info.recorded_at))
    + row(t('infoAddedAt'), formatDateTime(info.created_at))
    + row(t('infoFileModified'), media.modified ? formatDateTime(media.modified) : '')
    + row(t('infoProcessedAt'), info.completed_at ? formatDateTime(info.completed_at) : ''));
  const formatCard = card(t('infoFormat'),
    row(t('infoContainer'), probe.container)
    + row(t('infoDuration'), formatTime(probe.duration || info.duration), { mono: true })
    + row(t('infoVideo'), v ? [v.codec && v.codec.toUpperCase(), v.width && v.height ? `${v.width}×${v.height}` : '', v.fps ? `${v.fps} fps` : ''].filter(Boolean).join(' · ') : t('rdPlyrNo'))
    + (a ? row(t('infoAudio'), [a.codec && a.codec.toUpperCase(), a.sample_rate ? `${(a.sample_rate / 1000).toFixed(1)} kHz` : '', a.layout || (a.channels ? `${a.channels} ch` : ''), a.bitrate_kbps ? `${a.bitrate_kbps} kbps` : ''].filter(Boolean).join(' · ')) : '')
    + row(t('infoBitrate'), probe.bitrate_kbps ? `${probe.bitrate_kbps} kbps` : ''));
  const recogCard = card(t('rdPlyrInfoRecognition'),
    row(t('infoModel'), formatModelName(m.transcript_model || info.model_name))
    + row(t('rdPlyrInfoVersions'), (m.transcript_versions || []).length ? [m.transcript_model, ...(m.transcript_versions || []).map(x => x.model)].filter(Boolean).map(formatModelName).join(', ') : '')
    + row(t('infoLanguage'), !info.language ? '' : info.language === 'auto' ? t('studioSpeechAuto') : `${langName(info.language)} (${String(info.language).toUpperCase()})`)
    + row(t('infoCounts'), t('infoCountsValue').replace('{spk}', () => info.speakers).replace('{seg}', () => info.segments).replace('{words}', () => info.words))
    + row(t('infoAnalyses'), (info.summaries || []).map(k => tMaybe(`summaryName_${k}`) || ((state.summaryTemplates || []).find(x => x.id === k) || {}).name || k).join(', '))
    + row(t('infoTranslations'), (info.translations || []).map(c => langName(c)).join(', ')),
    btn(t('rdPlyrRetranscribe'), 'rpInfoToRecognition'));
  const proc = m.processing || {};
  const soundCard = card(t('rdPlyrInfoSound'),
    flag(t('txProcDenoise'), proc.denoise !== false, t('rdPlyrOn'), t('rdPlyrOff'))
    + flag(t('txProcLoudnorm'), proc.loudnorm !== false, t('rdPlyrOn'), t('rdPlyrOff'))
    + flag(t('rdPlyrInfoLeveling'), proc.leveling !== false, t('rdPlyrOn'), t('rdPlyrOff'))
    + flag(t('rdPlyrInfoVad'), proc.vad !== false, t('rdPlyrOn'), t('rdPlyrOff'))
    + flag(t('txProcDiarization'), proc.diarization !== false, t('rdPlyrOn'), t('rdPlyrOff')),
    btn(t('rdPlyrChange'), 'rpInfoToRecognition'), t('rdPlyrInfoSoundNote'));
  const p = rpPlayer();
  const dubTrack = state.audioTrack && state.audioTrack !== 'orig';
  const listenCard = card(t('rdPlyrWhileListening'),
    flag(t('rdPlyrStudioVoice'), !!state.studioVoiceActive, t('rdPlyrOnM'), t('rdPlyrOffM'))
    + flag(t('rdPlyrSkipSilence'), !!state.skipSilence, t('rdPlyrOnM'), t('rdPlyrOffM'))
    + row(t('rdPlyrTrack'), dubTrack ? `${t('trackDub')} ${String(state.audioTrack).toUpperCase()}` : t('trackOriginal'))
    + row(t('rdPlyrSpeed'), rpSpeedLabel((p && p.playbackRate) || 1), { mono: true }),
    btn(t('rdPlyrSetUp'), 'rpInfoToSound'), t('rdPlyrInfoListenNote'));

  const kindLabel = (c) => c.kind === 'dub' ? `${t('rdPlyrCopy_dub')} ${String(c.lang || '').toUpperCase()}` : t(`rdPlyrCopy_${c.kind}`);
  const tint = { clean: ['rgba(10,132,255,0.16)', '#8CC4FF'], denoise: ['rgba(126,231,135,0.14)', '#7EE787'], dub: ['rgba(191,90,242,0.16)', '#D4B3FF'],
    highlights: ['rgba(255,179,64,0.14)', '#FFC56B'], audio: ['rgba(100,210,255,0.14)', '#64D2FF'], media: ['rgba(255,255,255,0.08)', '#C7C7CC'] };
  const copies = rp.copies || [];
  const copyRows = copies.map(c => {
    const [bg, fg] = tint[c.kind] || tint.media;
    return `<div class="ri-copy">
      <span class="ri-copy-icon" style="background:${bg};color:${fg}" aria-hidden="true"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg></span>
      <span style="min-width:0;display:flex;flex-direction:column;gap:1px"><span class="ri-copy-name" title="${escapeHtml(c.path)}">${escapeHtml(c.name)}</span><span class="ri-copy-sub">${escapeHtml(kindLabel(c))}${c.exists ? '' : ' · ' + escapeHtml(t('infoMissing'))}</span></span>
      <span class="ri-copy-sub" style="text-align:right">${c.size != null ? escapeHtml(formatBytes(c.size)) : ''}</span>
      <span class="ri-copy-sub">${c.modified ? escapeHtml(formatDateTime(c.modified)) : ''}</span>
      <span style="display:inline-flex;gap:6px">${c.exists ? btn(t('rdPlyrListen'), 'rpPlayCopy', [c.id]) + btn(t('showInFolder'), 'rpRevealCopy', [c.id]) : ''}${c.deletable ? `<button class="rp-small-btn rp-item-danger" data-click="rpDeleteCopy" data-click-args='${jsArg([c.id])}'>${escapeHtml(t('rdPlyrDelete'))}</button>` : ''}</span>
    </div>`;
  }).join('');
  const pending = (info.pending_exports || []).length ? `<div class="rounded-xl border border-apple-amber/40 bg-apple-amber/10 px-4 py-3 text-xs text-apple-amber flex items-center justify-between gap-3">
      <span>${escapeHtml(t('infoPendingExports').replace('{n}', () => info.pending_exports.length))}</span>
      <button data-click="rpCancelPendingExports" data-click-args='[${Number(info.id)}]' class="ui-btn ui-btn-subtle h-6 px-2 text-[11.5px]">${escapeHtml(t('cancel'))}</button></div>` : '';
  box.innerHTML = `<div class="ri-grid">${fileCard}${datesCard}${formatCard}${recogCard}${soundCard}${listenCard}</div>
    <section class="ri-card"><div class="ri-card-head"><h3 class="ri-card-title" style="flex:none">${escapeHtml(t('rdPlyrCopies'))}</h3><span class="ri-copy-sub">${copies.length || ''}</span>
      <span style="flex:1"></span><span class="ri-copy-sub">${escapeHtml(t('rdPlyrCopiesNote'))}</span></div>
      ${copyRows || `<p class="ri-note" style="margin:4px 14px 6px">${escapeHtml(t('rdPlyrNoCopies'))}</p>`}</section>${pending}`;
}

async function rpCancelPendingExports(id) {
  await fetch(`/api/meetings/${id}/pending-exports`, { method: 'DELETE' });
  rpOpenInfo();
}

function rpInfoToRecognition() {
  closeModal('recinfo');
  openRecordTab('transcript');
  setTimeout(() => document.getElementById('tx-proc-btn')?.click(), 80);
}

function rpInfoToSound() {
  closeModal('recinfo');
  if (state.appView !== 'library') openRecordTab('transcript');
  setTimeout(() => { rp.open = 'sound'; rpRenderPop(); }, 60);
}

async function rpRevealCopy(cid) {
  const m = state.activeMeeting;
  if (!m) return;
  const res = await fetch(`/api/meetings/${m.id}/copies/${encodeURIComponent(cid)}/reveal`, { method: 'POST' });
  if (!res.ok) showToast(errorText(await readJson(res).catch(() => ({})), res));
}

async function rpDeleteCopy(cid) {
  const m = state.activeMeeting;
  const c = (rp.copies || []).find(x => x.id === cid);
  if (!m || !c) return;
  if (!(await askConfirm(t('rdPlyrDeleteCopyConfirm').replace('{name}', () => c.name), true, t('cfDeleteFile')))) return;
  const res = await fetch(`/api/meetings/${m.id}/copies/${encodeURIComponent(cid)}`, { method: 'DELETE' });
  const data = await readJson(res).catch(() => ({}));
  if (!res.ok) { showToast(errorText(data, res)); return; }
  rp.copies = data;
  rpRenderInfo();
  showToast(t('rdPlyrCopyDeleted'));
}

// «Слушать»: the copy plays in the player; the toast brings the recording back
function rpPlayCopy(cid) {
  const m = state.activeMeeting;
  const c = (rp.copies || []).find(x => x.id === cid);
  const p = rpPlayer();
  if (!m || !c || !p) return;
  closeModal('recinfo');
  state.audioTrack = 'orig';
  p.src = `/api/meetings/${m.id}/copies/${encodeURIComponent(cid)}/file`;
  p.play().catch(() => {});
  showToast(t('rdPlyrPlayingCopy').replace('{name}', () => c.name), {
    label: t('rdPlyrBackToRecording'),
    onClick: () => { p.src = `/api/meetings/${m.id}/media`; rpRender(); },
  });
}

// --- Keys of the player (not while typing, not over a dialog) ---
document.addEventListener('keydown', (e) => {
  if (e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.key === 'Escape' && rp.open) { e.preventDefault(); rpClosePop(); return; }
  if (isTypingTarget(e.target) || anyModalOpen()) return;
  const p = rpPlayer();
  if (state.appView !== 'library' || !state.activeMeeting || !p || !p.src) return;
  if (e.code === 'KeyL') { e.preventDefault(); rpToggleLoop(); }
  else if (e.code === 'KeyA' && !e.shiftKey) { e.preventDefault(); rpSetA(); }
  else if (e.code === 'KeyB' && !e.shiftKey) { e.preventDefault(); if (rp.ab && rp.ab.b != null) rpClearAb(); else rpSetB(); }
  else if (e.code === 'KeyM') { e.preventDefault(); if (e.shiftKey) rpNextMark(); else rpMarkMoment(); }
});

// A click outside the open popover closes it
document.addEventListener('pointerdown', (e) => {
  if (!rp.open) return;
  if (e.target.closest && e.target.closest('.rp-anchor')) return;
  rpClosePop();
}, true);

document.addEventListener('DOMContentLoaded', () => {
  try { state.skipSilence = localStorage.getItem('verbascribe_skip_silence') === '1'; } catch (e) {}
  rpInitWave();
  const p = rpPlayer();
  if (!p) return;
  p.addEventListener('play', rpStartTicking);
  p.addEventListener('seeked', () => { rpRender(); rpRing(p); rpUpdateEndTime(); });
  p.addEventListener('ratechange', () => rpSetSpeed(p.playbackRate));
  p.addEventListener('loadedmetadata', () => { rpComputeBarSpeakers(); rpRenderChapters(); rpRender(); rpUpdateEndTime(); rpRenderVideoButton(); });
  p.addEventListener('timeupdate', () => { if (rp.remain) rpUpdateEndTime(); });
  p.addEventListener('enterpictureinpicture', rpRenderVideoButton);
  p.addEventListener('leavepictureinpicture', rpRenderVideoButton);
  rpRenderSoundChip();
});
