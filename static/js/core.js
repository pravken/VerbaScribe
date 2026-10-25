// VerbaScribe page script, part 1 of 9: State, small helpers, dialogs and voice profiles.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

// VerbaScribe UI logic. Depends on vendor/marked.min.js, vendor/purify.min.js and i18n.js.
const DONE_STATUSES = ['completed', 'failed', 'cancelled', 'cleared'];
// The page opened, so the locked page (app/main.py) may again try one fresh address next time
try { sessionStorage.removeItem('vs_relocked'); } catch (e) { /* storage off */ }

// --- Interface scale (Settings → Interface, ui_scale): CSS zoom on the page, 100 % by default ---
// The zoom is on <html>, so the page lays itself out in its own pixels: a point of the window
// (event.clientX/Y, getBoundingClientRect) is divided by the zoom before it becomes style.left/top.
const UI_SCALES = ['75', '90', '100', '115'];
function uiZoom() { return Number(document.documentElement.dataset.zoom) || 1; }
function zpx(v) { return v / uiZoom(); }
function viewW() { return window.innerWidth / uiZoom(); }
function viewH() { return window.innerHeight / uiZoom(); }
// Audit 3.15. After the page has drawn what was just shown (two frames), not after a guessed delay
function nextFrame(fn) { requestAnimationFrame(() => requestAnimationFrame(fn)); }
// «Show less motion» in Windows: scrolls jump instead of gliding (CSS covers the rest)
function scrollMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
}
// Audit 3.6. Menus and dialogs leave the way they came, in 120 ms, instead of vanishing in one
// frame; the pointer goes through them meanwhile, so nothing waits for the animation. Showing the
// element again cancels its exit (cancelLeave)
function leaveThen(el, done) {
  if (!el || scrollMotion() === 'auto' || el.classList.contains('hidden')) { done(); return; }
  const token = el._leave = (el._leave || 0) + 1;
  el.classList.add('is-leaving');
  const end = () => {
    if (el._leave !== token) return;
    el._leave++;
    el.classList.remove('is-leaving');
    done();
  };
  el.addEventListener('animationend', (e) => { if (e.target === el) end(); }, { once: true });
  setTimeout(end, 140);
}
function hideWithExit(el) { leaveThen(el, () => el.classList.add('hidden')); }
function removeWithExit(el) { leaveThen(el, () => el.remove()); }
function cancelLeave(el) {
  if (!el || !el.classList.contains('is-leaving')) return;
  el._leave = (el._leave || 0) + 1;
  el.classList.remove('is-leaving');
}

// Puts a shown menu at the pointer by its real size: where it does not fit below, it opens upward
// from the pointer, and where it does not fit to the right, leftward; it never leaves the window
function placeMenuAt(menu, e) {
  cancelLeave(menu);
  const x = zpx(e.clientX), y = zpx(e.clientY);
  const w = menu.offsetWidth, h = menu.offsetHeight;
  const left = x + w <= viewW() - 8 ? x : Math.max(8, x - w);
  const top = y + h <= viewH() - 8 ? y : Math.max(8, Math.min(y - h, viewH() - h - 8));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.style.transformOrigin = `${left < x ? 'right' : 'left'} ${top < y ? 'bottom' : 'top'}`;
}
function applyUiScale(pct) {
  const z = (UI_SCALES.includes(String(pct)) ? Number(pct) : 100) / 100;
  const root = document.documentElement;
  root.style.zoom = String(z);
  root.dataset.zoom = String(z);
  root.style.setProperty('--ui-zoom', String(z));  // body height: 100vh is not zoomed (styles.css)
  try { localStorage.setItem('verbascribe_ui_scale', String(Math.round(z * 100))); } catch (e) {}
  if (typeof scheduleFitTopbar === 'function') scheduleFitTopbar();
}
// Before anything is drawn: the last scale, so the window does not jump when the settings arrive
(() => { let v = null; try { v = localStorage.getItem('verbascribe_ui_scale'); } catch (e) {} applyUiScale(v || '100'); })();

// --- Events without inline handlers ---
// The page's Content-Security-Policy runs no inline script, so markup does not say onclick="…".
// An element names a page function in data-<event> and its arguments, as a JSON list, in
// data-<event>-args: <button data-click="openModal" data-click-args='["settings"]'>. In templates the
// values go in with jsArg() (or Number()), as before. Placeholders in the list: {"$":"event"},
// {"$":"el"} (the element), {"$":"value"}, {"$":"checked"}, {"$":"files"} and {"$":"data","k":"x"}
// (el.dataset.x). A text from a recording can never become a placeholder: jsArg makes it a string.
const PAGE_EVENTS = ['click', 'dblclick', 'change', 'input', 'keydown', 'contextmenu', 'dragstart', 'dragover', 'dragleave', 'drop'];
const PAGE_OWN_EVENTS = { focusin: 'focus', focusout: 'blur', error: 'error' };  // of the element itself

// The event as an inline handler saw it: its currentTarget is the element that has the handler
// (the listener is on the document, so the real one is the document). Methods act on the real event.
function pageEvent(ev, el) {
  return new Proxy(ev, {
    get(target, key) {
      if (key === 'currentTarget') return el;
      const value = Reflect.get(target, key, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
    set(target, key, value) { target[key] = value; return true; },
  });
}

function pageArg(a, el, ev) {
  if (!a || typeof a !== 'object' || Array.isArray(a) || typeof a.$ !== 'string') return a;
  switch (a.$) {
    case 'event': return pageEvent(ev, el);
    case 'el': return el;
    case 'value': return el.value;
    case 'checked': return el.checked;
    case 'files': return el.files;
    case 'data': return el.dataset[a.k];
    default: return a;
  }
}

function runPageHandler(el, type, ev) {
  const name = el.getAttribute('data-' + type);
  const fn = /^[A-Za-z_$][\w$]*$/.test(name || '') ? window[name] : null;
  if (typeof fn !== 'function') { console.error(`data-${type}: no page function "${name}"`); return; }
  let args = [];
  const raw = el.getAttribute(`data-${type}-args`);
  if (raw) {
    try { args = JSON.parse(raw); } catch (e) { console.error(`data-${type}-args: not JSON`, raw); return; }
  }
  fn.apply(el, args.map(a => pageArg(a, el, ev)));
}

for (const type of PAGE_EVENTS) {
  // Registered before every other listener of the page: the handlers run first, as inline ones did
  document.addEventListener(type, ev => {
    // From the element up, like bubbling; stopPropagation() in a handler ends it, and also keeps
    // the page's own document listeners (closing menus) from seeing the event, as before
    for (const el of ev.composedPath()) {
      if (el === document || !(el instanceof Element)) break;
      if (el.hasAttribute('data-' + type)) {
        runPageHandler(el, type, ev);
        if (ev.cancelBubble) { ev.stopImmediatePropagation(); break; }
      }
    }
  });
}
for (const [native, type] of Object.entries(PAGE_OWN_EVENTS)) {
  document.addEventListener(native, ev => {
    const el = ev.target;
    if (el instanceof Element && el.hasAttribute('data-' + type)) runPageHandler(el, type, ev);
  }, true);  // focus and a failed image do not bubble: seen on the way down
}

// Both attributes for a template: pageCall('click', 'quickTranslate', [lang]) gives
// data-click="quickTranslate" data-click-args='["en"]'. Any value is safe in the list.
function pageCall(type, fn, args = []) {
  return `data-${type}="${fn}"` + (args.length ? ` data-${type}-args='${escapeHtml(JSON.stringify(args))}'` : '');
}

// Small steps the markup used to write inline. `name`: a page function, called with the rest
function stopEvent(ev) { ev.stopPropagation(); }
function preventEvent(ev) { ev.preventDefault(); }
function stopThen(ev, name, ...args) { ev.stopPropagation(); window[name](...args); }
function preventThen(ev, name, ...args) { ev.preventDefault(); window[name](...args); }
function onEnter(ev, name, ...args) { if (ev.key === 'Enter') window[name](...args); }
function blurOnEnter(ev, el) { if (ev.key === 'Enter') el.blur(); }
function selectOnFocus(el) { el.select(); }
function clearField(id) { document.getElementById(id).value = ''; }
function clickElement(id) { document.getElementById(id).click(); }
function showFixed2(el, id) { document.getElementById(id).textContent = Number(el.value).toFixed(2); }
function removeElement(el) { el.remove(); }
function openSupport() { openExternal(SUPPORT_URL); }
// Several calls in turn: runAll(["hideTranslateMenu"], ["openTranslateModal"])
function runAll(...calls) { for (const [name, ...args] of calls) window[name](...args); }

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
  pencil: `<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/></svg>`,
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

// --- Dates in the interface language: a native date field writes its date the way the browser's own
// locale does (10/25/2026 in a Russian interface). Each one gets a button over it with the date as the
// interface writes it (25.10.2026); the button opens the field's own calendar. ---
function uiDateText(iso) {
  if (!iso) return t('pickDate');
  const d = new Date(`${iso}T00:00`);
  return isNaN(d) ? iso : d.toLocaleDateString(state.uiLang === 'en' ? 'en-US' : 'ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
function syncDateField(input) {
  const btn = input && input.parentElement && input.parentElement.querySelector(':scope > .vs-date-btn');
  if (btn) btn.textContent = uiDateText(input.value);
}
function enhanceDateInput(input) {
  if (input.dataset.vsDate) return;
  input.dataset.vsDate = '1';
  const wrap = document.createElement('span');
  wrap.className = 'vs-date';
  input.replaceWith(wrap);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = `${input.className} vs-date-btn`;
  btn.title = input.title;
  btn.addEventListener('click', () => { try { input.showPicker(); } catch (e) { input.focus(); } });
  input.tabIndex = -1;
  input.setAttribute('aria-hidden', 'true');
  input.addEventListener('change', () => syncDateField(input));
  wrap.append(input, btn);
  syncDateField(input);
}
function enhanceDateInputs(root) {
  if (root.matches && root.matches('input[type="date"]')) enhanceDateInput(root);
  if (root.querySelectorAll) root.querySelectorAll('input[type="date"]').forEach(enhanceDateInput);
}
(function watchDateInputs() {
  const start = () => {
    enhanceDateInputs(document.body);
    new MutationObserver(list => list.forEach(m => m.addedNodes.forEach(n => { if (n.nodeType === 1) enhanceDateInputs(n); })))
      .observe(document.body, { childList: true, subtree: true });
  };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
})();

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
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
  });

  renderMeetingsSidebar();
  loadDictionary();
  if (typeof scheduleFitTopbar === 'function') scheduleFitTopbar();
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
  document.querySelectorAll('.vs-date > input').forEach(syncDateField);
  if (typeof renderStatusbar === 'function') renderStatusbar();
  loadSystemInfo().then(() => { if (typeof renderStatusbar === 'function') renderStatusbar(); }).catch(() => {});
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
// opts.cancel: the label of the other button; opts.single: one button only (a message, not a question)
function confirmDialog(title, text, okLabel, danger = true, opts = {}) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 modal-backdrop z-[70] flex items-center justify-center p-4';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.innerHTML = `<div class="w-full max-w-md bg-apple-card border apple-border rounded-2xl shadow-2xl p-5 space-y-4 modal-card">
      <div class="text-sm font-semibold text-white">${escapeHtml(title)}</div>
      ${text ? `<p class="text-xs text-apple-secondary leading-relaxed whitespace-pre-line break-words">${escapeHtml(text)}</p>` : ''}
      <div class="flex justify-end gap-2">
        ${opts.single ? '' : `<button data-a="no" class="ui-btn ui-btn-subtle">${escapeHtml(opts.cancel || t('cancel'))}</button>`}
        <button data-a="yes" class="ui-btn ${danger ? 'bg-apple-red text-white hover:opacity-90' : 'ui-btn-primary'}">${escapeHtml(okLabel)}</button>
      </div></div>`;
    const done = v => { removeWithExit(wrap); document.removeEventListener('keydown', onKey, true); resolve(v); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); done(false); } };
    wrap.addEventListener('click', e => {
      const a = e.target.closest('button[data-a]');
      if (e.target === wrap || (a && a.dataset.a === 'no')) done(false);
      else if (a) done(true);
    });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(wrap);
    wrap.querySelector('button[data-a="no"]') ? wrap.querySelector('button[data-a="no"]').focus() : wrap.querySelector('button[data-a="yes"]').focus();
  });
}

// Short forms for the old browser dialogs (window.confirm / prompt / alert), in the app's style.
// danger: the button is red (deleting, discarding); said by the caller, not guessed from the words
// The question is the title and the button names the action («Удалить папку», not «OK»).
// text: the question with its explanation, in either order ("Удалить X? Записи останутся." or
// "Места мало. Начать запись?"); okLabel: the verb of the action
function askConfirm(text, danger = false, okLabel = '') {
  const [title, body] = splitQuestion(text);
  return confirmDialog(title, body, okLabel || t('ok'), danger);
}
function splitQuestion(text) {
  const s = String(text);
  const i = s.indexOf('?');
  if (i < 0) return [t('confirmTitle'), s];
  const rest = s.slice(i + 1).trim();
  if (rest) return [s.slice(0, i + 1).trim(), rest];
  const m = s.match(/^([\s\S]*[.!])\s+([^.!?]+\?)$/);
  return m ? [m[2].trim(), m[1].trim()] : [s.trim(), ''];
}

function askText(label, value = '', opts = {}) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 modal-backdrop z-[70] flex items-center justify-center p-4';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.innerHTML = `<form class="w-full max-w-md bg-apple-card border apple-border rounded-2xl shadow-2xl p-5 space-y-4 modal-card">
      ${opts.title ? `<div class="text-sm font-semibold text-white">${escapeHtml(opts.title)}</div>` : ''}
      <label class="block text-xs text-apple-label leading-relaxed whitespace-pre-line">${escapeHtml(label)}</label>
      <input type="text" class="w-full h-9 bg-apple-sidebar border apple-border rounded-lg px-3 text-sm text-white focus:outline-none focus:border-apple-blue" />
      <div class="flex justify-end gap-2">
        <button type="button" data-a="no" class="ui-btn ui-btn-subtle">${escapeHtml(t('cancel'))}</button>
        <button type="submit" class="ui-btn ui-btn-primary">${escapeHtml(opts.ok || t('ok'))}</button>
      </div></form>`;
    const input = wrap.querySelector('input');
    input.value = value || '';
    const done = v => { removeWithExit(wrap); document.removeEventListener('keydown', onKey, true); resolve(v); };
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
  return confirmDialog(t('attentionTitle'), String(text), t('gotIt'), false, { single: true }).then(() => {});
}

// The browser's reasons for not giving a microphone or the screen (DOMException names), in words a user
// can act on; what: 'mic' or 'screen'. Our own messages (a plain Error) are shown as they are.
function mediaErrorText(err, what = 'mic') {
  const name = err && err.name;
  if (what === 'screen') {
    if (name === 'NotAllowedError' || name === 'SecurityError') return t('recErrScreenDenied');
    if (name === 'NotFoundError' || name === 'NotReadableError' || name === 'AbortError' || name === 'NotSupportedError') return t('recErrScreenFailed');
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return t('recErrMicMissing');
  if (name === 'NotAllowedError' || name === 'SecurityError') return t('recErrMicDenied');
  if (name === 'NotReadableError' || name === 'AbortError' || name === 'TrackStartError') return t('recErrMicBusy');
  if (err && typeof DOMException !== 'undefined' && err instanceof DOMException) return t('recErrUnknown');
  return serverText((err && err.message) || String(err || '')) || t('recErrUnknown');
}

// A recording that could not start: the reason and a way to the recording settings (one place for the
// studio, the red button of the top bar and the microphone tests)
async function showMediaError(err, what = 'mic') {
  console.warn('media:', err);
  const go = await confirmDialog(t('recErrTitle'), mediaErrorText(err, what), t('recErrOpenSettings'), false, { cancel: t('close') });
  if (go) openModal('settings', 'record');
}

// A failure as a person reads it: {short} — the cause in words (a lost network, a link that cannot be
// downloaded, or the server's message without the program's paths and exception names), {tech} — the
// whole technical text for «Подробнее» ('' when there is nothing more than the short one)
const NET_ERROR = /403 Forbidden|401 Unauthorized|407 Proxy|ConnectError|ConnectTimeout|ReadTimeout|timed? ?out|Name or service not known|getaddrinfo|Network is unreachable|Connection (refused|reset|aborted)|SSLError|CERTIFICATE_VERIFY|ProxyError|Max retries exceeded|Temporary failure in name resolution/i;
const LINK_ERROR = /^ERROR: \[|Unable to download webpage|Unsupported URL|Video unavailable|Private video|This video is not available|HTTP Error \d{3}/im;
function humanError(raw) {
  const text = String(raw || '').trim();
  if (!text) return { short: '', tech: '' };
  const first = text.split(/\n/)[0].trim();
  let short;
  const model = first.match(/^Не удалось скачать модель (.+?):/);
  if (model && NET_ERROR.test(text)) short = t('errModelNoNet').replace('{model}', () => model[1]);
  else if (LINK_ERROR.test(text)) short = t('errLinkDownload');
  else if (NET_ERROR.test(text)) short = t('errNoNetwork');
  else {
    short = first.replace(/\s*(?:[A-Za-z]:\\|\/)[^\s]*(?:\.py|-packages)[^\s]*/g, '')
      .replace(/^(?:[A-Za-z_][\w.]*(?:Error|Exception)):\s*/, '').replace(/[:;,]\s*$/, '').trim();
    short = serverText(short);
    if (short.length > 180) short = `${short.slice(0, 177)}…`;
  }
  return { short: short || t('errUnknown'), tech: text === short ? '' : text };
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

// A count with its word, shared by the pages: "1 файл / 2 файла / 5 файлов": the forms are given as "one|few|many" (English: "one|many|many")
function plural(n, forms) {
  const [one, few, many] = forms.split('|');
  const m10 = n % 10, m100 = n % 100;
  const form = state.uiLang === 'en' ? (n === 1 ? one : many)
    : m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
  return `${n} ${form}`;
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
  showWarning(t('sessionLost'));
  // In the app window the page reloads and signs in again (app/main.py _LOCKED_PAGE)
  if (window.pywebview && window.pywebview.api) setTimeout(() => location.replace('/'), 1500);
  /* in a browser: the toast says what to do */
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

// Audit 6.3, 3.11. Four kinds of notices: a status, done (with «Отменить» when it can be undone),
// a warning and an error. An error stays until it is closed and is read out by the screen reader at
// once; the others leave by themselves, but not while the pointer is on them. A new status or done
// notice replaces the previous one; errors and warnings stack, three at most, the newest below
const TOAST_LIFE = { status: 3000, success: 3000, warning: 8000, error: 0 };
const TOAST_ICONS = {
  status: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6.2"/><path d="M8 7.2v3.6M8 5.1v.1" stroke-linecap="round"/></svg>',
  success: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.4l3 3 6-6.4"/></svg>',
  warning: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.2l6.2 11H1.8z"/><path d="M8 6.6v3M8 11.4v.1"/></svg>',
  error: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="8" r="6.2"/><path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4"/></svg>',
};
function showToast(msg, action, kind) {
  kind = TOAST_LIFE[kind] !== undefined ? kind : (action ? 'success' : 'status');
  const stack = document.getElementById('toast');
  if (kind === 'status' || kind === 'success') {
    stack.querySelectorAll('.toast-status, .toast-success').forEach(old => { if (!old.classList.contains('is-leaving')) removeWithExit(old); });
  }
  const el = document.createElement('div');
  el.className = `toast toast-${kind}`;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  el.innerHTML = `<span class="toast-icon" aria-hidden="true">${TOAST_ICONS[kind]}</span><span class="toast-text"></span>`;
  el.querySelector('.toast-text').textContent = msg;
  const dismiss = () => { clearTimeout(el._timer); removeWithExit(el); };
  if (action && action.label && typeof action.onClick === 'function') {
    const btn = document.createElement('button');
    btn.className = 'toast-action';
    btn.textContent = action.label;
    if (action.title) btn.title = action.title;
    btn.onclick = () => { dismiss(); action.onClick(); };
    el.appendChild(btn);
  }
  if (kind === 'warning' || kind === 'error') {
    const x = document.createElement('button');
    x.className = 'toast-close';
    x.setAttribute('aria-label', t('close'));
    x.textContent = '✕';
    x.onclick = dismiss;
    el.appendChild(x);
  }
  const life = action && kind === 'success' ? 7000 : TOAST_LIFE[kind];
  const arm = (ms) => { clearTimeout(el._timer); if (ms) el._timer = setTimeout(dismiss, ms); };
  el.addEventListener('mouseenter', () => clearTimeout(el._timer));
  el.addEventListener('mouseleave', () => arm(life && Math.max(1500, life / 2)));
  stack.appendChild(el);
  const live = [...stack.children].filter(c => !c.classList.contains('is-leaving'));
  live.slice(0, Math.max(0, live.length - 3)).forEach(c => removeWithExit(c));
  arm(life);
  return dismiss;
}
// Audit 6.1. A reversible removal happens at once and the notice offers «Отменить» for 7 s; the
// server is told only then (or when the window closes), so undoing needs nothing from it.
// hide() and restore() change the page; commit(keepalive) sends the removal and resolves true
// when it went through (on false the item comes back with an error)
function deleteWithUndo({ message, hide, restore, commit }) {
  hide();
  let settled = false;
  const onLeave = () => { if (!settled) { settled = true; commit(true); } };
  const finish = async () => {
    if (settled) return;
    settled = true;
    window.removeEventListener('pagehide', onLeave);
    const ok = await Promise.resolve(commit(false)).catch(() => false);
    if (ok === false) { restore(); showError(t('errorGeneric')); }
  };
  window.addEventListener('pagehide', onLeave);
  const timer = setTimeout(finish, 7000);
  showToast(message, { label: t('rdSasUndo'), onClick: () => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    window.removeEventListener('pagehide', onLeave);
    restore();
  } });
}
function showError(msg, action) { return showToast(msg || t('errorGeneric'), action, 'error'); }
function showWarning(msg, action) { return showToast(msg, action, 'warning'); }

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
const SETTINGS_TABS = ['general', 'ui', 'record', 'recognition', 'models', 'analysis', 'calendar', 'hotkeys', 'dictionary',
  'export', 'privacy', 'updates', 'dub', 'bench', 'about'];
const SETTINGS_TAB_ALIASES = { integrations: 'export', interface: 'ui', ai: 'analysis', dict: 'dictionary', recog: 'recognition' };
function selectSettingsTab(name) {
  name = SETTINGS_TAB_ALIASES[name] || name;
  if (!SETTINGS_TABS.includes(name)) name = 'general';
  document.querySelectorAll('#modal-settings [data-stab]').forEach(el => el.classList.toggle('hidden', el.dataset.stab !== name));
  SETTINGS_TABS.forEach(k => {
    const btn = document.getElementById(`stab-${k}`);
    if (!btn) return;
    btn.classList.toggle('is-active', k === name);
    if (k === name) btn.setAttribute('aria-current', 'page'); else btn.removeAttribute('aria-current');
  });
  document.getElementById('modal-settings').dataset.tab = name;
  try { localStorage.setItem('verbascribe_settings_tab', name); } catch (e) {}
  if (name === 'bench' && typeof loadBench === 'function') loadBench();
  if (typeof onSettingsTabShown === 'function') onSettingsTabShown(name);  // static/js/settings.js
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
  if (name === 'settings') mountSettingsPage();  // the settings are a page of the main area (shell.js)
  const modal = document.getElementById(`modal-${name}`);
  _modalReturnFocus[name] = document.activeElement;
  if (name !== 'settings') {
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
  }
  cancelLeave(modal);
  modal.classList.remove('hidden');
  if (name === 'settings' && state.appView !== 'settings') switchAppView('settings');
  setTimeout(() => {
    if (modal.contains(document.activeElement)) return;
    const first = name === 'settings' ? document.getElementById('settings-search') : null;  // the search, not «Закрыть»
    (first || focusablesIn(modal)[0] || modal).focus();
  }, 60);
  if (name === 'import') onImportDialogOpen();  // importdlg.js: the list, the processing from the settings
  if (name === 'settings') {
    let last = null;
    try { last = localStorage.getItem('verbascribe_settings_tab'); } catch (e) {}
    selectSettingsTab(tab || last || 'general');
    syncSettingsFormFromState();  // settings.js: what the import window and the home page changed since
    loadVoiceProfiles();
    loadDubInfo();
    populateAudioInputDevices();
    refreshExportDirLabel();
    refreshRecordingsDirLabel();
    refreshObsidianStatus();
    state.llmProviderEdited = false;
    state.llmShowCloud = false;
    loadLlmStatus();
    nextFrame(() => drawIdleSoundCloudBars('settings-waveform-canvas'));
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
    box.innerHTML = `<div class="text-[11.5px] text-apple-tertiary">${escapeHtml(t('voiceProfilesNone'))}</div>`;
    return;
  }
  box.innerHTML = list.map(p => `
    <div class="flex items-center justify-between gap-2 text-xs">
      <span class="text-apple-label truncate">${escapeHtml(p.name)}
        <span class="text-apple-tertiary">· ${escapeHtml(t('voiceProfileSamples').replace('{n}', () => p.samples))}</span></span>
      <button data-click="deleteVoiceProfile" data-click-args='[${Number(p.id)}, {"$": "data", "k": "name"}]' data-name="${escapeHtml(p.name)}"
        class="ui-btn ui-btn-subtle h-6 px-2 text-[11.5px] text-apple-red" title="${escapeHtml(t('tipVoiceProfileDelete'))}">${escapeHtml(t('voiceProfileDelete'))}</button>
    </div>`).join('');
}

async function applyVoiceProfilesToLibrary() {
  const res = await fetch('/api/voice-profiles/apply-all', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showError(data.detail); return; }
  showToast(data.speakers ? t('voiceApplyAllDone').replace('{n}', () => data.speakers).replace('{m}', () => data.meetings) : t('voiceApplyAllNone'));
  if (data.speakers) {
    await loadMeetingsList();
    if (state.activeMeeting) selectMeeting(state.activeMeeting.id, false);
  }
}

async function deleteVoiceProfile(id, name) {
  if (!await askConfirm(t('voiceProfileDeleteConfirm').replace('{name}', () => name || ''), true, t('cfDeleteProfile'))) return;
  const res = await fetch(`/api/voice-profiles/${id}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showError(data.detail);
    return;
  }
  renderVoiceProfiles(data);
}

function closeModal(name) {
  if (name === 'settings') {
    stopSettingsAudioTest();
  }
  if (name === 'about') endAboutEgg();  // the transcript of the room does not go on unseen
  const modal = document.getElementById(`modal-${name}`);
  if (name === 'settings') modal.classList.add('hidden');
  else hideWithExit(modal);
  // The settings page: back to the view it was opened from
  if (name === 'settings' && state.appView === 'settings') switchAppView(state.prevView || 'home');
  const back = _modalReturnFocus[name];
  if (back && document.contains(back)) back.focus();
}
function closeModalOnBackdrop(e, name) {
  if (e.target === e.currentTarget) closeModal(name);
}

// Audit 4.5. Scrolled lists fade into their edges instead of being cut by a line: the top edge
// fades only once the list is scrolled, the bottom one while there is more below
function softEdges(el) {
  if (!el || el._softEdges) return;
  el._softEdges = true;
  el.classList.add('soft-edges');
  const update = () => {
    el.classList.toggle('se-top', el.scrollTop > 2);
    el.classList.toggle('se-bottom', el.scrollTop + el.clientHeight < el.scrollHeight - 2);
  };
  el.addEventListener('scroll', update, { passive: true });
  if (window.ResizeObserver) new ResizeObserver(update).observe(el);
  if (window.MutationObserver) new MutationObserver(update).observe(el, { childList: true, subtree: true });
  update();
}
document.addEventListener('DOMContentLoaded', () => {
  ['transcript-container', 'an-main', 'library-answer', 'wview-speakers', 'wview-info'].forEach(id => softEdges(document.getElementById(id)));
});

// A press inside a dialog released over its backdrop (a text selection dragged out of a field)
// reaches the backdrop as a click: that is not a click on the backdrop, so no dialog closes on it
let _pressTarget = null;
document.addEventListener('pointerdown', e => { _pressTarget = e.target; }, true);
window.addEventListener('click', e => {
  const t = e.target;
  if (t === _pressTarget || !(t instanceof Element)) return;
  if (t.classList.contains('modal-backdrop') || t.classList.contains('tk-veil')) e.stopPropagation();
}, true);

function switchWorkspaceTab(tab) {
  state.activeWorkspaceTab = tab;
  if (typeof renderTopbar === 'function') renderTopbar();
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
