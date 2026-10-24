// VerbaScribe page script: the speakers of a recording and the people of all recordings (package C,
// docs/redesign-plan.md). The parts are classic scripts sharing one global scope, loaded in order by index.html.
//
// «Участники»: a card per speaker (name with the directory's names to pick from, role, «Это я», a
// sample to hear, ⋯: «это один человек с…», «здесь два человека»), the voice the library knows, the
// people the calendar invited; who was mentioned; how the talk went. «Все люди»: the directory
// (app/people.py) with merging of spellings and people or departments added by hand.

// What the server knows around the open recording: calendar guests, mentions, the directory's names
const spCtx = { id: null, data: null, loading: null };

async function loadSpeakersContext(force) {
  const m = state.activeMeeting;
  if (!m) return null;
  if (!force && spCtx.id === m.id && spCtx.data) return spCtx.data;
  if (!force && spCtx.id === m.id && spCtx.loading) return spCtx.loading;
  spCtx.id = m.id;
  spCtx.loading = fetch(`/api/meetings/${m.id}/people`).then(r => r.ok ? r.json() : null).catch(() => null);
  const data = await spCtx.loading;
  spCtx.loading = null;
  if (spCtx.id === m.id) spCtx.data = data || { calendar: [], mentions: [], names: [] };
  return spCtx.data;
}

function spInitial(name) {
  const s = String(name || '').trim();
  const digits = s.match(/\d+$/);
  return digits && /^(Участник|Speaker)/i.test(s) ? digits[0] : (s[0] || '?').toUpperCase();
}

function spTint(color) {
  const c = /^#[0-9a-f]{6}$/i.test(color || '') ? color : '#0A84FF';
  return `${c}2E`;
}

function spIsDefault(name) { return !String(name || '').trim() || /^(Участник|Speaker|SPEAKER)[ _]?\d+$/i.test(String(name).trim()); }

// Seconds and lines of every speaker, read from the transcript
function spTalk(m) {
  const out = {};
  for (const seg of m.segments || []) {
    const r = out[seg.speaker] || (out[seg.speaker] = { lines: 0, seconds: 0 });
    r.lines += 1;
    r.seconds += Math.max(0, (Number(seg.end) || 0) - (Number(seg.start) || 0));
  }
  return out;
}

function spMinutes(sec) {
  const s = Math.round(sec || 0);
  return s >= 60 ? `${Math.floor(s / 60)} ${t('unitMin')}${s % 60 ? ` ${s % 60} ${t('unitSec')}` : ''}` : `${s} ${t('unitSec')}`;
}

// Typing a name or a role: a card rebuilt now would take the input away (and what was typed)
function spTyping(box) {
  const a = document.activeElement;
  return !!a && box.contains(a) && a.tagName === 'INPUT' && a.type !== 'color';
}

// background: a refresh nobody asked for (the context arrived); it waits while a card is being typed in
function renderSpeakersPanel(opts = {}) {
  renderTalkAnalytics();
  const box = document.getElementById('speakers-list');
  const m = state.activeMeeting;
  if (!box) return;
  const speakers = (m && m.speakers) || {};
  const keys = Object.keys(speakers);
  if (!keys.length) {
    box.innerHTML = `<div class="sp-empty">${escapeHtml(t('emptySpeakers'))}</div>`;
    document.getElementById('sp-mentions').innerHTML = '';
    return;
  }
  const ctx = spCtx.id === m.id ? spCtx.data : null;
  if (!ctx) loadSpeakersContext().then(() => { if (state.activeMeeting === m) renderSpeakersPanel({ background: true }); });
  const talk = spTalk(m);
  const total = Object.values(talk).reduce((a, r) => a + r.seconds, 0) || 1;
  const names = new Set(keys.map(k => String(speakers[k].name || '').trim().toLowerCase()));
  const guests = ((ctx && ctx.calendar) || []).filter(n => !names.has(n.toLowerCase())).slice(0, 8);
  const list = document.getElementById('sp-names');
  if (list && ctx) list.innerHTML = (ctx.names || []).map(p => `<option value="${escapeHtml(p.name)}"></option>`).join('');
  if (opts.background && spTyping(box)) {
    renderMentions(ctx);
    if (!box._spWait) {
      box._spWait = true;
      box.addEventListener('focusout', () => setTimeout(() => {
        box._spWait = false;
        if (state.activeMeeting === m) renderSpeakersPanel({ background: true });
      }, 0), { once: true });
    }
    return;
  }

  box.innerHTML = keys.map(id => {
    const s = speakers[id];
    const r = talk[id] || { lines: 0, seconds: 0 };
    const share = Math.round((r.seconds / total) * 100);
    const color = /^#[0-9a-f]{6}$/i.test(s.color || '') ? s.color : '#0A84FF';
    const stats = [`${r.lines} ${t('turns')}`, spMinutes(r.seconds), `${share}%`].join(' · ');
    const hints = [];
    if (s.voice_profile) hints.push(`<div class="sp-hint"><span class="sp-ok">✓</span>${escapeHtml(t('spKnownVoice'))}</div>`);
    if (s.voice_suggest) {
      const pct = s.voice_suggest.score ? ` · ${escapeHtml(t('spMatch'))} ${Math.round(s.voice_suggest.score * 100)}%` : '';
      hints.push(`<div class="sp-ask">
          <span class="flex-1">${escapeHtml(t('spLooksLike'))} <b>${escapeHtml(s.voice_suggest.name)}</b>${pct}</span>
          <button class="sp-ask-yes" ${pageCall('click', 'spRename', [id, s.voice_suggest.name])}>${escapeHtml(t('spYesThem'))}</button>
          <button class="an-btn an-btn-sm" ${pageCall('click', 'dismissVoiceSuggestion', [id])}>${escapeHtml(t('voiceSuggestNo'))}</button>
        </div>`);
    }
    if (spIsDefault(s.name) && guests.length) {
      hints.push(`<div class="sp-guests"><span class="sp-note">${escapeHtml(t('spInvited'))}</span>
          ${guests.map(g => `<button class="sp-chip" ${pageCall('click', 'spRename', [id, g])}>${escapeHtml(g)}</button>`).join('')}
          <button class="sp-chip sp-chip-other" ${pageCall('click', 'spFocusName', [id])}>${escapeHtml(t('spOtherName'))}</button></div>`);
    }
    return `<div class="sp-card" data-spk="${escapeHtml(id)}">
      <div class="sp-top">
        <label class="sp-av" style="background:${spTint(color)};color:${color}" title="${escapeHtml(t('tipSpeakerColor'))}">${escapeHtml(spInitial(s.name))}
          <input type="color" value="${escapeHtml(color)}" ${pageCall('change', 'updateSpeaker', [id, null, { $: 'value' }])} class="sp-color"></label>
        <div class="sp-main">
          <div class="sp-line">
            <input class="sp-name" value="${escapeHtml(s.name || '')}" list="sp-names" aria-label="${escapeHtml(t('spNameAria'))}"
              title="${escapeHtml(t('tipSpeakerNameInput'))}" data-spk-name="${escapeHtml(id)}"
              ${pageCall('blur', 'spRename', [id, { $: 'value' }])} ${pageCall('keydown', 'blurOnEnter', [{ $: 'event' }, { $: 'el' }])}>
            <button class="sp-me${s.is_me ? ' sp-me-on' : ''}" aria-pressed="${s.is_me ? 'true' : 'false'}" title="${escapeHtml(t('spMeTip'))}"
              ${pageCall('click', 'spToggleMe', [id])}>${escapeHtml(t('spMe'))}</button>
          </div>
          <input class="sp-role" value="${escapeHtml(s.role || '')}" placeholder="${escapeHtml(t('spRolePh'))}" aria-label="${escapeHtml(t('spRolePh'))}"
            ${pageCall('blur', 'spSetRole', [id, { $: 'value' }])} ${pageCall('keydown', 'blurOnEnter', [{ $: 'event' }, { $: 'el' }])}>
          <span class="sp-stats">${escapeHtml(stats)}</span>
        </div>
        <button class="an-btn" title="${escapeHtml(t('tipPlayVoiceSample'))}"
          ${pageCall('click', 'previewSpeakerVoice', [Number(s.sample_start) || 0, Number(s.sample_end) || 5])}>${SVG_ICONS.playMini}<span>${escapeHtml(t('listenBtn'))}</span></button>
        <button class="an-dots" aria-label="${escapeHtml(t('spMoreTip'))}" title="${escapeHtml(t('spMoreTip'))}" ${pageCall('click', 'openSpeakerCardMenu', [id, { $: 'el' }])}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></button>
      </div>
      ${hints.join('')}
    </div>`;
  }).join('');
  renderMentions(ctx);
}

// People and departments named in the text who do not speak in it
function renderMentions(ctx) {
  const box = document.getElementById('sp-mentions');
  if (!box) return;
  const items = (ctx && ctx.mentions) || [];
  if (!items.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="sp-head sp-head-sm"><h3 class="sp-h3">${escapeHtml(t('spMentioned'))}</h3>
      <span class="sp-note">${escapeHtml(t('spMentionedNote'))}</span></div>
    <div class="sp-mgrid">${items.map(x => `<button class="sp-mrow" ${pageCall('click', 'findInTranscript', [x.name])} title="${escapeHtml(t('spFindInText'))}">
        <span class="sp-mav${x.kind === 'dept' ? ' sp-mav-dept' : ''}">${escapeHtml(x.kind === 'dept' ? '▦' : spInitial(x.name))}</span>
        <span class="sp-mtext"><b>${escapeHtml(x.name)}</b><span>${escapeHtml(t(x.kind === 'dept' ? 'spDept' : 'spPerson'))} · ${escapeHtml(t('spTimes').replace('{n}', () => String(x.count)))}</span></span>
        <span class="sp-mtime">${formatTime(x.first_at)}</span>
      </button>`).join('')}</div>`;
}

// A new name everywhere in the recording; the old one can be brought back from the toast
async function spRename(id, value) {
  const m = state.activeMeeting;
  const s = m && (m.speakers || {})[id];
  const name = String(value || '').trim();
  if (!s || !name || name === s.name) return;
  await renameSpeaker(id, name);
  loadSpeakersContext(true).then(() => { if (state.activeMeeting === m) renderSpeakersPanel({ background: true }); });
}

function spFocusName(id) {
  const el = document.querySelector(`[data-spk-name="${CSS.escape(id)}"]`);
  if (el) { el.focus(); el.select(); }
}

async function spMeta(id, body) {
  const m = state.activeMeeting;
  if (!m) return null;
  const res = await fetch(`/api/meetings/${m.id}/speakers/meta`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ speaker_id: id, ...body }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(serverText(data.detail || 'Error')); return null; }
  if (body.is_me !== undefined) await spRefreshSettings();
  if (state.activeMeeting === m) {
    state.activeMeeting = data;
    renderSpeakersPanel();
  }
  return data;
}

function spSetRole(id, value) {
  const s = ((state.activeMeeting && state.activeMeeting.speakers) || {})[id];
  if (!s || String(value || '').trim() === String(s.role || '')) return;
  spMeta(id, { role: String(value || '').trim() });
}

async function spToggleMe(id) {
  const s = ((state.activeMeeting && state.activeMeeting.speakers) || {})[id];
  if (!s) return;
  if (!s.is_me && spIsDefault(s.name)) { showToast(t('spMeNeedsName')); spFocusName(id); return; }
  if (await spMeta(id, { is_me: !s.is_me }) && !s.is_me) showToast(t('spMeSet').replace('{name}', () => s.name));
}

// ⋯ of a card: one person counted as two (merge), two counted as one (split), their lines in the text
function openSpeakerCardMenu(id, el) {
  const m = state.activeMeeting;
  const speakers = (m && m.speakers) || {};
  const s = speakers[id];
  if (!s) return;
  const others = Object.keys(speakers).filter(k => k !== id);
  const r = el.getBoundingClientRect();
  const items = [];
  if (others.length) {
    items.push({ label: t('spSameAs'), disabled: true });
    others.forEach(k => items.push({ label: `   ${speakers[k].name || k}`, run: () => spMerge(id, k) }));
    items.push({ sep: true });
  }
  items.push({ label: t('spSplit'), run: () => openSplitDialog(id) },
    { label: t('spShowLines'), run: () => { openRecordTab('transcript'); state.speakerFilter = null; filterBySpeaker(id); } },
    { label: t('listenBtn'), run: () => previewSpeakerVoice(Number(s.sample_start) || 0, Number(s.sample_end) || 5) });
  setTimeout(() => openTextMenuAt(items, r.right, r.bottom + 6, { right: true }), 0);
}

async function spMerge(source, target) {
  const m = state.activeMeeting;
  const sp = (m && m.speakers) || {};
  if (!sp[source] || !sp[target]) return;
  const ok = await confirmDialog(t('spMergeTitle').replace('{a}', () => sp[source].name).replace('{b}', () => sp[target].name),
    t('spMergeText').replace('{b}', () => sp[target].name), t('spMergeBtn'), false);
  if (!ok) return;
  const res = await fetch(`/api/meetings/${m.id}/speakers/merge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source, target }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(serverText(data.detail || 'Error')); return; }
  spAfterTranscriptEdit(m, data);
  showToast(t('spMerged').replace('{name}', () => data.speakers[target].name));
}

function spAfterTranscriptEdit(m, data) {
  if (state.activeMeeting !== m) return;
  state.activeMeeting = data;
  if (state.speakerFilter && !(data.speakers || {})[state.speakerFilter]) state.speakerFilter = null;
  renderSpeakerPillsBar();
  renderTranscript();
  renderSpeakersPanel();
}

// «Здесь два человека»: the lines the other person said go to a new speaker
function openSplitDialog(id) {
  const m = state.activeMeeting;
  const s = m && (m.speakers || {})[id];
  if (!s) return;
  const lines = (m.segments || []).map((seg, i) => ({ seg, i })).filter(x => x.seg.speaker === id);
  if (lines.length < 2) { showToast(t('spSplitFew')); return; }
  document.getElementById('sp-split-dlg')?.remove();
  const wrap = document.createElement('div');
  wrap.id = 'sp-split-dlg';
  wrap.className = 'fixed inset-0 modal-backdrop z-[60] flex items-center justify-center p-4';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.innerHTML = `<div class="an-dlg sp-dlg-split modal-card">
      <div class="an-dlg-head"><h2>${escapeHtml(t('spSplitTitle'))}</h2><button data-a="no" class="an-dlg-x" aria-label="${escapeHtml(t('close'))}">✕</button></div>
      <p class="an-dlg-text">${escapeHtml(t('spSplitText').replace('{name}', () => s.name))}</p>
      <div class="sp-lines">${lines.map(x => `<label class="sp-lrow">
          <input type="checkbox" data-pos="${x.i}">
          <span class="sp-ltime">${formatTime(x.seg.start)}</span>
          <span class="sp-ltext">${escapeHtml(x.seg.text || '')}</span>
          <button type="button" class="sp-lplay" data-play="${Number(x.seg.start) || 0}" title="${escapeHtml(t('listenBtn'))}">▶</button>
        </label>`).join('')}</div>
      <label class="sp-split-name"><span>${escapeHtml(t('spSecondPerson'))}</span>
        <input data-f="name" class="an-fld" list="sp-names" placeholder="${escapeHtml(t('spNamePh'))}"></label>
      <div class="an-dlg-foot">
        <span class="sp-note" data-f="count"></span><span class="flex-1"></span>
        <button data-a="no" class="an-btn">${escapeHtml(t('cancel'))}</button>
        <button data-a="ok" class="an-btn an-btn-primary" disabled>${escapeHtml(t('spSplitBtn'))}</button>
      </div></div>`;
  const count = () => {
    const n = wrap.querySelectorAll('input[data-pos]:checked').length;
    wrap.querySelector('[data-f="count"]').textContent = t('spPicked').replace('{n}', () => String(n)).replace('{total}', () => String(lines.length));
    wrap.querySelector('[data-a="ok"]').disabled = n === 0 || n === lines.length;
  };
  wrap.addEventListener('change', count);
  wrap.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (e.target === wrap || (b && b.dataset.a === 'no')) { wrap.remove(); return; }
    if (b && b.dataset.play !== undefined) { e.preventDefault(); previewSpeakerVoice(Number(b.dataset.play), Number(b.dataset.play) + 6); return; }
    if (!b || b.dataset.a !== 'ok') return;
    const positions = [...wrap.querySelectorAll('input[data-pos]:checked')].map(x => Number(x.dataset.pos));
    const res = await fetch(`/api/meetings/${m.id}/speakers/split`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speaker_id: id, positions, new_name: wrap.querySelector('[data-f="name"]').value }),
    });
    const data = await readJson(res);
    if (!res.ok) { showToast(serverText(data.detail || 'Error')); return; }
    wrap.remove();
    spAfterTranscriptEdit(m, data);
    showToast(t('spSplitDone').replace('{n}', () => String(positions.length)));
  });
  document.body.appendChild(wrap);
  count();
}

// --- How the talk went: numbers, the share of time, who spoke when (click: hear it), interruptions ---
async function renderTalkAnalytics() {
  const box = document.getElementById('talk-analytics');
  const m = state.activeMeeting;
  if (!box || !m || !(m.segments || []).length) { if (box) box.innerHTML = ''; return; }
  const a = await fetch(`/api/meetings/${m.id}/analytics`).then(r => r.ok ? r.json() : null).catch(() => null);
  if (!a || state.activeMeeting !== m) return;
  const segs = m.segments || [];
  const dur = Number(m.duration) || Math.max(...segs.map(s => Number(s.end) || 0), 1);
  const speakers = m.speakers || {};
  const color = (id) => (speakers[id] && /^#[0-9a-f]{6}$/i.test(speakers[id].color || '') ? speakers[id].color : '#8E8E93');
  const name = (id) => (speakers[id] && speakers[id].name) || id;
  // Who cut in on whom: a line that starts before the other's line ends
  const pairs = {};
  for (let i = 1; i < segs.length; i++) {
    const p = segs[i - 1], c = segs[i];
    if (p.speaker !== c.speaker && (Number(c.start) || 0) < (Number(p.end) || 0) - 0.2) {
      const k = `${c.speaker}→${p.speaker}`;
      pairs[k] = (pairs[k] || 0) + 1;
    }
  }
  const metric = (value, label) => `<div class="sp-metric"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`;
  const questions = a.speakers.reduce((x, r) => x + (r.questions || 0), 0);
  const longest = a.longest_monologues && a.longest_monologues[0];
  const ids = Object.keys(speakers);
  const lanes = ids.map(id => {
    const bars = segs.filter(s => s.speaker === id).map(s => {
      const left = ((Number(s.start) || 0) / dur) * 100, w = Math.max(0.25, (((Number(s.end) || 0) - (Number(s.start) || 0)) / dur) * 100);
      return `<span style="left:${left.toFixed(2)}%;width:${w.toFixed(2)}%;background:${color(id)}"></span>`;
    }).join('');
    return `<div class="sp-lane-row"><span class="sp-lane-name" style="color:${color(id)}">${escapeHtml(name(id))}</span>
      <button class="sp-lane" aria-label="${escapeHtml(t('spLaneAria'))}" ${pageCall('click', 'spSeekLane', [{ $: 'event' }])}>${bars}</button></div>`;
  }).join('');
  const ticks = [0, 1 / 3, 2 / 3, 1].map(f => `<span>${formatTime(dur * f)}</span>`).join('');
  const pairRows = Object.entries(pairs).sort((x, y) => y[1] - x[1]).slice(0, 6).map(([k, n]) => {
    const [from, to] = k.split('→');
    return `<div class="sp-int"><span style="color:${color(from)}">${escapeHtml(name(from))}</span><span class="sp-arrow">→</span>
      <span style="color:${color(to)}">${escapeHtml(name(to))}</span><span class="sp-fill"></span><span class="sp-num">${n}</span></div>`;
  }).join('');
  box.innerHTML = `<h2 class="sp-h2">${escapeHtml(t('spHowTalk'))}</h2>
    <div class="sp-metrics">
      ${metric(spMinutes(a.talk_seconds), t('analyticsTalk'))}${metric(`${a.silence_share}%`, t('analyticsSilence'))}
      ${metric(String(a.turns), t('spTurnsLabel'))}${metric(spMinutes(a.avg_turn), t('spAvgTurn'))}
      ${metric(String(questions), t('spQuestions'))}${metric(longest ? spMinutes(longest.seconds) : '—', t('spLongest'))}
    </div>
    <div class="sp-card2"><span class="sp-cap">${escapeHtml(t('spShare'))}</span>
      <div class="sp-sharebar">${a.speakers.map(r => `<span style="width:${r.share}%;background:${r.color}" title="${escapeHtml(r.name)} ${r.share}%"></span>`).join('')}</div>
      <div class="sp-legend">${a.speakers.map(r => `<span><i style="background:${r.color}"></i>${escapeHtml(r.name)} <em>${r.share}%</em></span>`).join('')}</div>
    </div>
    <div class="sp-card2"><span class="sp-cap">${escapeHtml(t('spWhoWhen'))} <em>· ${escapeHtml(t('spWhoWhenHint'))}</em></span>
      ${lanes}<div class="sp-lane-row sp-ticks"><span></span><span class="sp-tickline">${ticks}</span></div>
    </div>
    ${pairRows ? `<div class="sp-card2"><span class="sp-cap">${escapeHtml(t('spInterrupts'))}</span>${pairRows}</div>` : ''}
    ${a.longest_monologues.length ? `<div class="sp-card2"><span class="sp-cap">${escapeHtml(t('analyticsMonologues'))}</span>${a.longest_monologues.map(x =>
      `<div class="sp-int"><button class="an-tc" ${pageCall('click', 'seekPlayer', [Number(x.start) || 0])}>${formatTime(x.start)}</button>
        <span>${escapeHtml(x.name)}</span><span class="sp-fill"></span><span class="sp-num">${escapeHtml(spMinutes(x.seconds))}</span></div>`).join('')}</div>` : ''}`;
}

// A click on a lane: the player goes to that moment
function spSeekLane(ev) {
  const m = state.activeMeeting;
  const el = ev.currentTarget;
  if (!m || !el) return;
  const r = el.getBoundingClientRect();
  const segs = m.segments || [];
  const dur = Number(m.duration) || Math.max(...segs.map(s => Number(s.end) || 0), 1);
  const ratio = Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width)));
  seekPlayer(ratio * dur);
}

// --- «Все люди»: everyone the library knows, people and departments, with merging ---
const dir = { items: [], tab: 'person', q: '', picked: [], adding: false };

async function openPeopleDirectory() {
  document.getElementById('sp-dir-dlg')?.remove();
  dir.picked = [];
  dir.q = '';
  dir.adding = false;
  const wrap = document.createElement('div');
  wrap.id = 'sp-dir-dlg';
  wrap.className = 'fixed inset-0 modal-backdrop z-[60] flex items-center justify-center p-4';
  wrap.setAttribute('role', 'dialog');
  wrap.setAttribute('aria-modal', 'true');
  wrap.innerHTML = `<div class="an-dlg sp-dir modal-card">
      <div class="an-dlg-head"><h2>${escapeHtml(t('spAllPeople'))}</h2><button data-a="close" class="an-dlg-x" aria-label="${escapeHtml(t('close'))}">✕</button></div>
      <p class="an-dlg-text">${escapeHtml(t('spDirText'))}</p>
      <div class="sp-dir-bar">
        <div class="sp-seg" role="radiogroup" aria-label="${escapeHtml(t('spDirWho'))}" data-f="tabs"></div>
        <label class="sp-search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>
          <input data-f="q" placeholder="${escapeHtml(t('spFind'))}" aria-label="${escapeHtml(t('spFind'))}"></label>
        <button data-a="merge" class="an-btn an-btn-primary" disabled></button>
      </div>
      <div class="sp-dir-head"><span></span><span></span><span>${escapeHtml(t('spColName'))}</span><span>${escapeHtml(t('spColWhere'))}</span><span>${escapeHtml(t('spColVoice'))}</span><span>${escapeHtml(t('spColTasks'))}</span><span></span></div>
      <div class="sp-dir-list" data-f="list"></div>
      <div class="an-dlg-foot" data-f="foot"></div></div>`;
  document.body.appendChild(wrap);
  wrap.addEventListener('input', e => {
    if (e.target.dataset.f === 'q') { dir.q = e.target.value; drawDirectory(); }
  });
  wrap.addEventListener('change', e => {
    const k = e.target.dataset.pick;
    if (k === undefined) return;
    dir.picked = e.target.checked ? dir.picked.concat(k) : dir.picked.filter(x => x !== k);
    drawDirectory();
  });
  wrap.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.stopPropagation(); wrap.remove(); }
    if (e.key === 'Enter' && e.target.dataset.f === 'new') wrap.querySelector('[data-a="add-ok"]')?.click();
  });
  wrap.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (e.target === wrap || (b && b.dataset.a === 'close')) { wrap.remove(); return; }
    if (!b) return;
    if (b.dataset.tab) { dir.tab = b.dataset.tab; dir.picked = []; drawDirectory(); return; }
    if (b.dataset.a === 'merge') { await dirMerge(); return; }
    if (b.dataset.a === 'add') { dir.adding = true; drawDirectory(); wrap.querySelector('[data-f="new"]')?.focus(); return; }
    if (b.dataset.a === 'add-no') { dir.adding = false; drawDirectory(); return; }
    if (b.dataset.a === 'add-ok') { await dirAdd(wrap.querySelector('[data-f="new"]').value); return; }
    if (b.dataset.me !== undefined) { await dirUpdate({ name: b.dataset.me, is_me: b.dataset.on !== '1' }); return; }
    if (b.dataset.edit !== undefined) {
      const p = dir.items.find(x => x.name === b.dataset.edit);
      const name = await askText(t('spDirRename').replace('{name}', () => p.name), p.name);
      if (name === null || !name.trim() || name.trim() === p.name) return;
      const role = await askText(t('spDirRole').replace('{name}', () => name.trim()), p.role || '');
      await dirUpdate({ name: p.name, new_name: name.trim(), ...(role !== null ? { role } : {}) });
    }
  });
  await loadDirectory();
}

async function loadDirectory() {
  const res = await fetch('/api/people/directory');
  dir.items = res.ok ? await res.json() : [];
  drawDirectory();
}

function drawDirectory() {
  const wrap = document.getElementById('sp-dir-dlg');
  if (!wrap) return;
  const n = (kind) => dir.items.filter(p => p.kind === kind).length;
  wrap.querySelector('[data-f="tabs"]').innerHTML = [['person', t('spPeople')], ['dept', t('spDepts')]].map(([k, label]) =>
    `<button role="radio" aria-checked="${dir.tab === k}" data-tab="${k}" class="sp-seg-btn${dir.tab === k ? ' sp-seg-on' : ''}">${escapeHtml(label)} <span>${n(k)}</span></button>`).join('');
  const q = dir.q.trim().toLowerCase();
  const rows = dir.items.filter(p => p.kind === dir.tab && (!q || p.name.toLowerCase().includes(q) || (p.aliases || []).some(a => a.toLowerCase().includes(q)) || (p.role || '').toLowerCase().includes(q)));
  const where = (p) => [p.recordings ? t('spInRecords').replace('{n}', () => String(p.recordings)) : '', p.calendar ? t('spInCalendar') : '', p.tasks && !p.recordings ? t('spInTasks') : '']
    .filter(Boolean).join(' · ') || t('spAddedByHand');
  wrap.querySelector('[data-f="list"]').innerHTML = rows.map(p => {
    const on = dir.picked.includes(p.name);
    const sub = [p.role, (p.aliases || []).length ? `${t('spAlso')}: ${p.aliases.join(', ')}` : ''].filter(Boolean).join(' · ');
    return `<label class="sp-drow${on ? ' sp-drow-on' : ''}">
        <input type="checkbox" data-pick="${escapeHtml(p.name)}" ${on ? 'checked' : ''} aria-label="${escapeHtml(t('spPick'))}">
        <span class="sp-mav${p.kind === 'dept' ? ' sp-mav-dept' : ''}">${escapeHtml(p.kind === 'dept' ? '▦' : spInitial(p.name))}</span>
        <span class="sp-dname"><b>${escapeHtml(p.name)}${p.is_me ? ` <em>${escapeHtml(t('spMe'))}</em>` : ''}</b><span>${escapeHtml(sub)}</span></span>
        <span class="sp-dcell">${escapeHtml(where(p))}</span>
        <span class="sp-dcell${p.voice ? ' sp-voice' : ''}">${escapeHtml(p.voice ? `● ${t('spSample')}` : '—')}</span>
        <span class="sp-dcell sp-dtasks">${p.tasks || '—'}</span>
        <span class="sp-dact">
          ${p.kind === 'person' ? `<button class="sp-dbtn" data-me="${escapeHtml(p.name)}" data-on="${p.is_me ? '1' : ''}" title="${escapeHtml(t('spMeTip'))}">${escapeHtml(t(p.is_me ? 'spNotMe' : 'spMe'))}</button>` : ''}
          <button class="sp-dbtn" data-edit="${escapeHtml(p.name)}" title="${escapeHtml(t('spDirEditTip'))}">✎</button>
        </span>
      </label>`;
  }).join('') || `<div class="sp-empty">${escapeHtml(t(q ? 'spNothingFound' : 'spDirEmpty'))}</div>`;
  const merge = wrap.querySelector('[data-a="merge"]');
  merge.disabled = dir.picked.length < 2;
  merge.textContent = dir.picked.length >= 2 ? t('spMergeN').replace('{n}', () => String(dir.picked.length)) : t('spMergeBtn');
  merge.title = t('spMergeTip');
  wrap.querySelector('[data-f="foot"]').innerHTML = dir.adding
    ? `<input data-f="new" class="an-fld sp-new" maxlength="120" placeholder="${escapeHtml(t(dir.tab === 'dept' ? 'spNewDeptPh' : 'spNamePh'))}">
       <button data-a="add-ok" class="an-btn an-btn-primary">${escapeHtml(t('spAdd'))}</button><button data-a="add-no" class="an-btn">${escapeHtml(t('cancel'))}</button>`
    : `<button data-a="add" class="an-btn">+ ${escapeHtml(t(dir.tab === 'dept' ? 'spAddDept' : 'spAddPerson'))}</button>
       <span class="flex-1"></span><span class="sp-note">${escapeHtml(t('spVoiceLocal'))}</span>`;
}

async function dirAdd(name) {
  if (!String(name || '').trim()) return;
  const res = await fetch('/api/people/directory', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, kind: dir.tab }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(serverText(data.detail || 'Error')); return; }
  dir.adding = false;
  await loadDirectory();
  spCtx.data = null;
}

async function dirUpdate(body) {
  const res = await fetch('/api/people/directory', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(serverText(data.detail || 'Error')); return; }
  if (body.is_me !== undefined || body.new_name) await spRefreshSettings();
  await loadDirectory();
  spCtx.data = null;
}

// «Это я» writes the setting my_name on the server (the home page and the tasks read it): fetch it back
async function spRefreshSettings() {
  const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: {} }) });
  const data = await readJson(res);
  if (res.ok && state.system) state.system.settings = data.settings;
}

async function dirMerge() {
  if (dir.picked.length < 2) return;
  const [keep, ...rest] = dir.picked;
  const ok = await confirmDialog(t('spDirMergeTitle').replace('{name}', () => keep),
    t('spDirMergeText').replace('{names}', () => rest.join(', ')).replace('{name}', () => keep), t('spMergeBtn'), false);
  if (!ok) return;
  const res = await fetch('/api/people/merge', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names: dir.picked }),
  });
  const data = await readJson(res);
  if (!res.ok) { showToast(serverText(data.detail || 'Error')); return; }
  dir.picked = [];
  await loadDirectory();
  spCtx.data = null;
  showToast(t('spDirMerged').replace('{name}', () => data.name));
}
