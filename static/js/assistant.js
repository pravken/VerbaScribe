// The assistant («Ассистент»): conversations about the recordings. Each conversation keeps its
// questions and answers (app/assistant_chats.py, on this computer only); an answer comes from
// /api/library/ask over the scope chosen under the question: the whole library, a folder, a period,
// a series, a person or chosen recordings. The scope helpers of library.js (askScopeLabel,
// askScopePayload) stay in use; the functions below replace the old view of library.js.

const ASSIST = { chats: [], loaded: false, chatId: null, query: '', saveTimer: null, aborts: new Map() };

// --- Conversations: loaded once, saved a moment after each change ---
async function assistantLoad() {
  if (ASSIST.loaded) return;
  ASSIST.loaded = true;
  const data = await fetch('/api/assistant/chats').then(r => (r.ok ? r.json() : { chats: [] })).catch(() => ({ chats: [] }));
  ASSIST.chats = Array.isArray(data.chats) ? data.chats : [];
  ASSIST.chatId = ASSIST.chats[0]?.id || null;
  renderAssistant();
}

function assistantSaveSoon() {
  clearTimeout(ASSIST.saveTimer);
  ASSIST.saveTimer = setTimeout(async () => {
    const chats = ASSIST.chats.filter(c => c.items.length);  // an empty new conversation is not kept
    const res = await fetch('/api/assistant/chats', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chats }),
    }).catch(() => null);
    if (res && !res.ok) showToast(errorText(await readJson(res), res));
  }, 500);
}

function currentChat() {
  return ASSIST.chats.find(c => c.id === ASSIST.chatId) || null;
}

function newChatObject() {
  const now = new Date().toISOString();
  return { id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`, title: '', created: now, updated: now, model: '', items: [] };
}

function assistantNewChat() {
  const cur = currentChat();
  if (!cur || cur.items.length) {
    const chat = newChatObject();
    ASSIST.chats.unshift(chat);
    ASSIST.chatId = chat.id;
  }
  renderAssistant();
  document.getElementById('library-question')?.focus();
}

function chatTitle(c) {
  return c.title || c.items[0]?.question || t('rdSasNewChat');
}

function chatDay(iso) {
  const d = new Date(iso || 0);
  const start = new Date(); start.setHours(0, 0, 0, 0);
  if (d >= start) return 'today';
  if (d >= new Date(start.getTime() - 86400000)) return 'yesterday';
  return 'earlier';
}

function assistantFilterChats(value) {
  ASSIST.query = String(value || '').trim().toLowerCase();
  renderChatList();
}

function renderChatList() {
  const box = document.getElementById('as-chat-list');
  if (!box) return;
  const q = ASSIST.query;
  const chats = ASSIST.chats.filter(c => c.items.length || c.id === ASSIST.chatId)
    .filter(c => !q || `${chatTitle(c)} ${c.items.map(i => i.question).join(' ')}`.toLowerCase().includes(q));
  if (!chats.length) {
    box.innerHTML = `<div class="as-chat-empty">${escapeHtml(t(q ? 'rdSasNoChatsFound' : 'rdSasNoChats'))}</div>`;
    return;
  }
  const groups = { today: [], yesterday: [], earlier: [] };
  chats.forEach(c => groups[chatDay(c.updated || c.created)].push(c));
  const label = { today: t('rdSasToday'), yesterday: t('rdSasYesterday'), earlier: t('rdSasEarlier') };
  box.innerHTML = Object.entries(groups).filter(([, list]) => list.length).map(([k, list]) => `
    <div class="as-group">${escapeHtml(label[k])}</div>
    ${list.map(c => {
      const d = new Date(c.updated || c.created);
      const when = k === 'earlier' ? d.toLocaleDateString(state.uiLang === 'en' ? 'en-GB' : 'ru-RU', { day: 'numeric', month: 'short' })
        : d.toLocaleTimeString(state.uiLang === 'en' ? 'en-GB' : 'ru-RU', { hour: '2-digit', minute: '2-digit' });
      const n = c.items.length;
      return `<div class="as-chat ${c.id === ASSIST.chatId ? 'is-active' : ''}">
        <button data-click="assistantOpenChat" data-click-args='[${jsArg(c.id)}]' class="as-chat-main">
          <span class="as-chat-title">${escapeHtml(chatTitle(c))}</span>
          <span class="as-chat-meta">${escapeHtml(when)}${n ? ` · ${escapeHtml(t('rdSasQuestions').replace('{n}', () => n))}` : ''}</span>
        </button>
        <button data-click="assistantDeleteChat" data-click-args='[${jsArg(c.id)}]' class="as-chat-x" title="${escapeHtml(t('rdSasDeleteChat'))}" aria-label="${escapeHtml(t('rdSasDeleteChat'))}">${SVG_ICONS.closeX}</button>
      </div>`;
    }).join('')}`).join('');
}

function assistantOpenChat(id) {
  ASSIST.chatId = id;
  const chat = currentChat();
  const last = chat?.items[chat.items.length - 1];
  if (last?.scope && askScopeValidAny(last.scope)) state.askScope = last.scope;
  renderAssistant();
}

function assistantDeleteChat(id) {
  const at = ASSIST.chats.findIndex(c => c.id === id);
  if (at < 0) return;
  const [chat] = ASSIST.chats.splice(at, 1);
  for (const item of chat.items) ASSIST.aborts.get(item.id)?.abort();
  if (ASSIST.chatId === id) ASSIST.chatId = ASSIST.chats[0]?.id || null;
  renderAssistant();
  assistantSaveSoon();
  showToast(t('rdSasChatDeleted'), {
    label: t('rdSasUndo'),
    onClick: () => {
      ASSIST.chats.splice(Math.min(at, ASSIST.chats.length), 0, chat);
      ASSIST.chatId = chat.id;
      renderAssistant();
      assistantSaveSoon();
    },
  });
}

// --- The header: title (rename), the model that answers, where the text goes ---
function assistantRename() {
  const chat = currentChat();
  if (!chat || !chat.items.length) return;
  const input = document.getElementById('as-title-input');
  const title = document.getElementById('as-title');
  input.value = chatTitle(chat);
  title.classList.add('hidden');
  input.classList.remove('hidden');
  input.focus();
  input.select();
}

function assistantRenameDone(save = true) {
  const input = document.getElementById('as-title-input');
  if (!input || input.classList.contains('hidden')) return;
  const chat = currentChat();
  if (save && chat && input.value.trim()) {
    chat.title = input.value.trim().slice(0, 200);
    assistantSaveSoon();
  }
  input.classList.add('hidden');
  document.getElementById('as-title').classList.remove('hidden');
  renderAssistant();
}

function assistantTitleKey(e) {
  if (e.key === 'Enter') { e.preventDefault(); assistantRenameDone(true); }
  if (e.key === 'Escape') { e.preventDefault(); assistantRenameDone(false); }
}

function assistantSettings() {
  return state.system?.settings || {};
}

// '' — as in the settings; 'cloud' — the cloud model; an Ollama tag — that local model
function assistantModelIsCloud(model) {
  const s = assistantSettings();
  return model === 'cloud' || (!model && s.llm_provider === 'cloud');
}

function assistantModelName(model) {
  const s = assistantSettings();
  if (assistantModelIsCloud(model)) return s.llm_cloud_model || t('rdSasCloudModel');
  const tag = model || s.ollama_model || '';
  return tag ? formatModelName(tag) : t('rdSasNoModel');
}

function renderAssistantHead() {
  const chat = currentChat();
  const title = document.getElementById('as-title');
  if (title) title.textContent = chat && chat.items.length ? chatTitle(chat) : t('rdSasNewChat');
  const model = chat?.model || '';
  const label = document.getElementById('as-model-label');
  if (label) label.textContent = assistantModelName(model);
  const pill = document.getElementById('as-privacy');
  if (pill) {
    const cloud = assistantModelIsCloud(model);
    pill.className = `as-pill ${cloud ? 'as-pill-cloud' : 'as-pill-local'}`;
    pill.textContent = t(cloud ? 'rdSasPillCloud' : 'rdSasPillLocal');
    pill.title = t(cloud ? 'rdSasPillCloudHint' : 'rdSasPillLocalHint');
  }
}

// A small menu at a button: [{label, note, on, onClick} | 'sep' | {head}]
function assistantMenu(e, items, width = 270) {
  e.stopPropagation();
  document.getElementById('as-menu')?.remove();
  const menu = document.createElement('div');
  menu.id = 'as-menu';
  menu.className = 'fixed macos-menu z-50 as-menu';
  menu.style.width = `${width}px`;
  menu.innerHTML = items.map((it, i) => {
    if (it === 'sep') return '<div class="my-1 h-px bg-white/10"></div>';
    if (it.head) return `<div class="px-2.5 pt-1.5 pb-1 text-[10.5px] text-apple-tertiary">${escapeHtml(it.head)}</div>`;
    if (it.html) return `<div data-i="${i}">${it.html}</div>`;
    return `<button class="macos-menu-item" data-i="${i}" ${it.disabled ? 'disabled' : ''}>
      <span class="w-3.5 text-apple-blue shrink-0">${it.on ? '✓' : ''}</span><span class="flex-1 truncate">${escapeHtml(it.label)}</span>
      ${it.note != null ? `<span class="text-[10.5px] text-apple-tertiary shrink-0">${escapeHtml(String(it.note))}</span>` : ''}</button>`;
  }).join('');
  menu.addEventListener('click', ev => {
    ev.stopPropagation();
    const b = ev.target.closest('button[data-i]');
    if (!b) return;
    const it = items[Number(b.dataset.i)];
    if (!it?.onClick) return;
    if (!it.keepOpen) menu.remove();
    it.onClick(ev);
  });
  document.body.appendChild(menu);
  const r = e.currentTarget.getBoundingClientRect();
  const h = menu.offsetHeight;  // layout pixels, like zpx()
  const left = Math.min(zpx(r.left), viewW() - width - 8);
  // under the button, or above it when the button is low in the window
  const top = zpx(r.bottom) + 4 + h > viewH() - 8 ? zpx(r.top) - 4 - h : zpx(r.bottom) + 4;
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
  return menu;
}
document.addEventListener('click', ev => {
  const menu = document.getElementById('as-menu');
  if (menu && !menu.contains(ev.target)) menu.remove();
});

function assistantModelMenu(e) {
  const chat = currentChat() || (() => { const c = newChatObject(); ASSIST.chats.unshift(c); ASSIST.chatId = c.id; return c; })();
  const s = assistantSettings();
  const local = ((state.system?.ollama?.catalog) || []).filter(m => m.installed).map(m => m.id);
  const pick = (model) => () => { chat.model = model; renderAssistantHead(); assistantSaveSoon(); };
  const items = [{ head: t('rdSasWhoAnswers') },
    { label: t('rdSasModelDefault'), note: assistantModelName(''), on: !chat.model, onClick: pick('') }];
  local.forEach(id => items.push({ label: formatModelName(id), note: t('rdSasLocal'), on: chat.model === id, onClick: pick(id) }));
  if (s.llm_cloud_model) items.push({ label: s.llm_cloud_model, note: t('rdSasCloud'), on: chat.model === 'cloud', onClick: pick('cloud') });
  items.push('sep', { label: t('rdSasModelsKeys'), onClick: () => openModal('settings', 'analysis') });
  assistantMenu(e, items, 290);
}

// The ⋯ of the conversation
function assistantChatMenu(e) {
  const chat = currentChat();
  const has = !!(chat && chat.items.length);
  assistantMenu(e, [
    { label: t('rdSasRename'), disabled: !has, onClick: () => assistantRename() },
    { label: t('rdSasCopyChat'), disabled: !has, onClick: () => assistantCopyChat() },
    { label: t('rdSasSaveChat'), note: '.md', disabled: !has, onClick: () => assistantSaveChat() },
    'sep',
    { label: t('rdSasNewChat'), note: 'Ctrl+N', onClick: () => assistantNewChat() },
    { label: t('rdSasDeleteChat'), disabled: !has, onClick: () => assistantDeleteChat(chat.id) },
  ], 260);
}

function assistantChatText(chat) {
  return [`# ${chatTitle(chat)}`, ...chat.items.filter(i => i.status === 'done').map(i => `\n## ${assistantAnswerText(i)}`)].join('\n');
}

async function assistantCopyChat() {
  const chat = currentChat();
  if (!chat) return;
  try {
    await navigator.clipboard.writeText(assistantChatText(chat));
    showToast(t('rdSasCopied'));
  } catch (e) {
    showToast(t('rdSasCopyFailed'));
  }
}

async function assistantSaveChat() {
  const chat = currentChat();
  if (!chat) return;
  const blob = new Blob([assistantChatText(chat)], { type: 'text/markdown' });
  await assistantSaveBlob(blob, `${chatTitle(chat).replace(/[\\/:*?"<>|\r\n\t]+/g, ' ').trim().slice(0, 80) || 'chat'}.md`);
}

// --- Where to look: the scopes of library.js and two more, a period and chosen recordings ---
function assistantMeetingDate(m) {
  return String(m.meeting_at || m.created_at || '').slice(0, 10);
}

function assistantScopeIds(scope) {
  if (scope.type === 'period') {
    const today = new Date();
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    let from = scope.from, to = scope.to;
    if (scope.days) { const d = new Date(today); d.setDate(d.getDate() - scope.days + 1); from = iso(d); to = iso(today); }
    return state.meetings.filter(m => { const d = assistantMeetingDate(m); return d && d >= from && d <= to; }).map(m => m.id);
  }
  if (scope.type === 'pick') return (scope.ids || []).filter(id => state.meetings.some(m => m.id === id));
  return null;
}

function askScopeValidAny(scope) {
  if (scope.type === 'period') return !!(scope.days || (scope.from && scope.to));
  if (scope.type === 'pick') return (scope.ids || []).length > 0;
  return askScopeValid(scope);
}

function assistantScopeCount(scope) {
  const ids = assistantScopeIds(scope);
  if (ids) return ids.length;
  if (scope.type === 'all') return state.meetings.length;
  if (scope.type === 'folder') return meetingsInFolderTree(scope.id).length;
  if (scope.type === 'meeting') return 1;
  const p = askScopePayload(scope);
  return p.meeting_ids ? p.meeting_ids.length : null;
}

function assistantScopeLabel(scope) {
  const n = assistantScopeCount(scope);
  const tail = n != null ? ` · ${n}` : '';
  if (scope.type === 'period') {
    if (scope.days === 7) return t('rdSasScopeWeek') + tail;
    if (scope.days === 30) return t('rdSasScopeMonth') + tail;
    const f = (d) => d.split('-').reverse().join('.');
    return `${f(scope.from)}–${f(scope.to)}${tail}`;
  }
  if (scope.type === 'pick') return t('rdSasScopePicked') + tail;
  if (scope.type === 'all') return t('rdSasScopeAll') + tail;
  return askScopeLabel(scope);
}

function assistantScopePayload(scope) {
  const ids = assistantScopeIds(scope);
  return ids ? { meeting_ids: ids } : askScopePayload(scope);
}

// The old view's entry points, now the assistant's
function renderAskScope() {
  if (!askScopeValidAny(state.askScope)) state.askScope = { type: 'all' };
  const label = document.getElementById('ask-scope-label');
  if (label) label.textContent = assistantScopeLabel(state.askScope);
  assistantSyncSend();
}

function setAskScope(scope) {
  state.askScope = scope;
  renderAskScope();
  document.getElementById('library-question')?.focus();
}

function openLibraryAsk(question, scope) {
  if (scope) state.askScope = scope;
  switchAppView('ask');
  const input = document.getElementById('library-question');
  if (question) input.value = question;
  assistantSyncSend();
  input.focus();
}

function assistantScopeMenu(e) {
  const sc = state.askScope;
  const same = (a) => JSON.stringify(a) === JSON.stringify(sc);
  const items = [{ head: t('rdSasWhereLook') },
    { label: t('rdSasScopeAll'), note: state.meetings.length, on: sc.type === 'all', onClick: () => setAskScope({ type: 'all' }) },
    'sep', { head: t('rdSasPeriod') },
    { label: t('rdSasScopeWeek'), note: assistantScopeCount({ type: 'period', days: 7 }), on: same({ type: 'period', days: 7 }), onClick: () => setAskScope({ type: 'period', days: 7 }) },
    { label: t('rdSasScopeMonth'), note: assistantScopeCount({ type: 'period', days: 30 }), on: same({ type: 'period', days: 30 }), onClick: () => setAskScope({ type: 'period', days: 30 }) },
    { label: t('rdSasScopeCustom'), on: sc.type === 'period' && !sc.days, onClick: (ev) => assistantPeriodPanel(ev) },
  ];
  const folders = [];
  const walk = (parentId, depth) => {
    for (const f of sortedFolders(parentId)) {
      folders.push({ label: `${'  '.repeat(depth)}${folderName(f)}`, note: meetingsInFolderTree(f.id).length,
        on: sc.type === 'folder' && sc.id === f.id, onClick: () => setAskScope({ type: 'folder', id: f.id }) });
      walk(f.id, depth + 1);
    }
  };
  walk(null, 0);
  if (folders.length) items.push('sep', { head: t('rdSasFolders') }, ...folders);
  const series = state.series || [];
  if (series.length) {
    items.push('sep', { head: t('seriesTitle') }, ...series.slice(0, 8).map(s => ({ label: s.name, note: s.count,
      on: sc.type === 'series' && sc.id === s.id, onClick: () => setAskScope({ type: 'series', id: s.id }) })));
  }
  const people = state.people || [];
  if (people.length) {
    items.push('sep', { head: t('peopleTitle') }, ...people.slice(0, 8).map(p => ({ label: p.name, note: p.recordings.length,
      on: sc.type === 'person' && sc.name.toLowerCase() === p.name.toLowerCase(), onClick: () => setAskScope({ type: 'person', name: p.name }) })));
  }
  items.push('sep', { label: t('rdSasScopePickMenu'), note: sc.type === 'pick' ? sc.ids.length : null, on: sc.type === 'pick', onClick: (ev) => assistantPickPanel(ev) });
  const m = state.activeMeeting;
  if (m) items.push({ label: `${t('askScopeMeeting')}: ${m.title}`, on: sc.type === 'meeting' && sc.id === m.id, onClick: () => setAskScope({ type: 'meeting', id: m.id }) });
  const menu = assistantMenu(e, items, 300);
  menu.classList.add('as-menu-scroll');
}

// A period with two dates, inside the menu
function assistantPeriodPanel() {
  const btn = document.getElementById('as-scope-btn');
  const sc = state.askScope.type === 'period' && !state.askScope.days ? state.askScope : null;
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const menu = assistantMenu({ stopPropagation() {}, currentTarget: btn }, [
    { head: t('rdSasScopeCustom') },
    { html: `<div class="flex items-center gap-2 px-2.5 py-1.5 text-xs text-apple-label">
        <input type="date" id="as-period-from" value="${sc ? sc.from : monthAgo}" class="sx-time" title="${escapeHtml(t('rdSasFrom'))}" aria-label="${escapeHtml(t('rdSasFrom'))}">
        <span>—</span>
        <input type="date" id="as-period-to" value="${sc ? sc.to : today}" class="sx-time" title="${escapeHtml(t('rdSasTo'))}" aria-label="${escapeHtml(t('rdSasTo'))}">
      </div>` },
    { label: t('rdSasApply'), onClick: () => {
      const from = document.getElementById('as-period-from')?.value, to = document.getElementById('as-period-to')?.value;
      if (from && to) setAskScope({ type: 'period', from: from <= to ? from : to, to: from <= to ? to : from });
    } },
  ], 300);
  menu.addEventListener('click', ev => ev.stopPropagation());
}

// Chosen recordings: a list with checkboxes
function assistantPickPanel() {
  const btn = document.getElementById('as-scope-btn');
  const chosen = new Set(state.askScope.type === 'pick' ? state.askScope.ids : []);
  const list = [...state.meetings].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 80);
  const rows = list.map(m => `<label class="flex items-center gap-2 px-2.5 py-1 text-xs text-apple-label cursor-pointer hover:bg-white/5 rounded">
      <input type="checkbox" data-mid="${m.id}" ${chosen.has(m.id) ? 'checked' : ''} class="rounded bg-apple-sidebar border-white/20 text-apple-blue">
      <span class="flex-1 truncate">${escapeHtml(m.title)}</span><span class="text-[10.5px] text-apple-tertiary">${escapeHtml(assistantMeetingDate(m).split('-').reverse().join('.'))}</span></label>`).join('');
  const menu = assistantMenu({ stopPropagation() {}, currentTarget: btn }, [
    { head: t('rdSasScopePickMenu') },
    { html: `<div class="max-h-[300px] overflow-y-auto">${rows || `<div class="px-2.5 py-2 text-xs text-apple-tertiary">${escapeHtml(t('rdSasNoRecordings'))}</div>`}</div>` },
    'sep',
    { label: t('rdSasApply'), onClick: () => {
      const ids = [...document.querySelectorAll('#as-menu input[data-mid]:checked')].map(x => Number(x.dataset.mid));
      setAskScope(ids.length ? { type: 'pick', ids } : { type: 'all' });
    } },
  ], 340);
  menu.addEventListener('click', ev => { if (ev.target.matches('input')) ev.stopPropagation(); });
}

// --- Asking ---
function assistantPending(chat = currentChat()) {
  return chat ? chat.items.find(i => i.status === 'pending') : null;
}

function assistantSyncSend() {
  const btn = document.getElementById('library-ask-btn');
  const input = document.getElementById('library-question');
  if (!btn || !input) return;
  const pending = assistantPending();
  const empty = assistantScopeCount(state.askScope) === 0;
  btn.textContent = t(pending ? 'rdSasStop' : 'askBtn');
  btn.classList.toggle('as-send-stop', !!pending);
  btn.disabled = !pending && (empty || !input.value.trim());
  input.disabled = empty && !pending;
  const hint = document.getElementById('as-compose-hint');
  if (hint) hint.textContent = empty ? t('rdSasScopeEmpty') : t('rdSasComposeHint');
}

function assistantKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); assistantSend(); }
  if (e.key === 'Escape' && assistantPending()) { e.preventDefault(); assistantStop(assistantPending().id); }
}

function assistantSendOrStop() {
  const p = assistantPending();
  if (p) assistantStop(p.id);
  else assistantSend();
}

// The old entry point (search results, the selection menu): the assistant asks
function askLibrary() {
  return assistantSend();
}

async function assistantSend(text, scopeArg) {
  const input = document.getElementById('library-question');
  const question = String(text ?? input.value).trim();
  if (!question || assistantPending()) return;
  let chat = currentChat();
  if (!chat) { chat = newChatObject(); ASSIST.chats.unshift(chat); ASSIST.chatId = chat.id; }
  const scope = { ...(scopeArg || state.askScope) };
  const item = { id: newAskItemId(), question, scope, scopeLabel: assistantScopeLabel(scope), status: 'pending', data: null, error: '', at: new Date().toISOString() };
  chat.items.push(item);
  if (!chat.title) chat.title = question.slice(0, 80);
  chat.updated = item.at;
  // the newest conversation goes first in the list
  ASSIST.chats.splice(ASSIST.chats.indexOf(chat), 1);
  ASSIST.chats.unshift(chat);
  if (text == null) input.value = '';
  renderAssistant(true);
  const ctrl = new AbortController();
  ASSIST.aborts.set(item.id, ctrl);
  try {
    const res = await fetch('/api/library/ask', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctrl.signal,
      body: JSON.stringify({ question, lang: state.uiLang === 'en' ? 'en' : 'ru', model: chat.model || null, ...assistantScopePayload(scope) }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorText(data, res));
    item.data = data;
    item.status = 'done';
  } catch (e) {
    if (item.status === 'stopped') return;
    item.error = e.message || String(e);
    item.status = 'error';
  } finally {
    ASSIST.aborts.delete(item.id);
  }
  renderAssistant(true);
  assistantSaveSoon();
}

function assistantStop(itemId) {
  const item = assistantFindItem(itemId);
  if (!item || item.status !== 'pending') return;
  item.status = 'stopped';
  ASSIST.aborts.get(itemId)?.abort();
  renderAssistant();
  assistantSaveSoon();
}

function assistantFindItem(itemId) {
  for (const c of ASSIST.chats) {
    const it = c.items.find(i => i.id === itemId);
    if (it) return it;
  }
  return null;
}

function assistantRetry(itemId) {
  const item = assistantFindItem(itemId);
  if (item) assistantSend(item.question, item.scope);
}

// The question goes back to the field to be changed and asked again
function assistantEditQuestion(itemId) {
  const item = assistantFindItem(itemId);
  if (!item) return;
  if (item.scope && askScopeValidAny(item.scope)) state.askScope = item.scope;
  renderAskScope();
  const input = document.getElementById('library-question');
  input.value = item.question;
  assistantSyncSend();
  input.focus();
}

// A source of an answer opens the recording at its moment
function openLibrarySource(itemId, n) {
  const item = assistantFindItem(itemId) || (typeof askThread !== 'undefined' ? askThread.find(x => x.id === itemId) : null);
  const s = ((item && item.data && item.data.sources) || []).find(x => x.n === n);
  if (!s) return;
  switchAppView('library');
  selectMeetingAndSeek(s.meeting_id, s.start);
}

// --- Copy and save an answer ---
function assistantAnswerText(item) {
  const sources = (item.data?.sources || []).map(s => `[${s.n}] ${s.title} · ${s.date} · ${formatTime(s.start)} — ${s.speaker ? s.speaker + ': ' : ''}${s.text}`);
  return [item.question, '', item.data?.answer || '', ...(sources.length ? ['', t('libraryAskSources') + ':', ...sources] : [])].join('\n');
}

async function assistantCopy(itemId, btn) {
  const item = assistantFindItem(itemId);
  if (!item) return;
  try {
    await navigator.clipboard.writeText(assistantAnswerText(item));
    if (btn) { btn.textContent = t('rdSasCopied'); setTimeout(() => { btn.textContent = t('rdSasCopy'); }, 1600); }
  } catch (e) {
    showToast(t('rdSasCopyFailed'));
  }
}

function assistantSaveMenu(e, itemId) {
  const item = assistantFindItem(itemId);
  if (!item) return;
  const ids = [...new Set((item.data?.sources || []).map(s => s.meeting_id))];
  const one = item.scope?.type === 'meeting' ? item.scope.id : ids.length === 1 ? ids[0] : null;
  const items = [{ head: t('rdSasSaveAs') },
    { label: 'Word', note: '.docx', onClick: () => assistantExport(item, 'docx') },
    { label: 'Markdown', note: '.md', onClick: () => assistantExport(item, 'md') },
    { label: t('rdSasPlainText'), note: '.txt', onClick: () => assistantExport(item, 'txt') }];
  if (one) items.push('sep', { label: t('rdSasToNotes'), onClick: () => assistantToNotes(item, one) });
  assistantMenu(e, items, 250);
}

async function assistantExport(item, fmt) {
  const res = await fetch('/api/assistant/export', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fmt, question: item.question, answer: item.data?.answer || '', sources: item.data?.sources || [], lang: state.uiLang === 'en' ? 'en' : 'ru' }),
  });
  if (!res.ok) { showToast(errorText(await readJson(res), res)); return; }
  await assistantSaveBlob(await res.blob(), `${item.question.replace(/[\\/:*?"<>|\r\n\t]+/g, ' ').trim().slice(0, 80) || 'answer'}.${fmt}`);
}

// The Windows "Save as" window where the page has it, a download otherwise
async function assistantSaveBlob(blob, name) {
  if (typeof window.showSaveFilePicker === 'function') {
    try {
      const handle = await window.showSaveFilePicker({ suggestedName: name });
      const w = await handle.createWritable();
      await w.write(blob);
      await w.close();
      showToast(`${t('exportSavedTo')} ${handle.name}`);
      return;
    } catch (err) {
      if (err && (err.name === 'AbortError' || err.code === 20)) return;
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 4000);
  showToast(`${t('exportSavedTo')} ${name}`);
}

// The answer is added to the end of the recording's own notes
async function assistantToNotes(item, meetingId) {
  const m = await fetch(`/api/meetings/${meetingId}`).then(r => (r.ok ? r.json() : null)).catch(() => null);
  if (!m) return;
  const notes = `${(m.user_notes || '').trimEnd()}\n\n${item.question}\n${item.data?.answer || ''}`.trim();
  const res = await fetch(`/api/meetings/${meetingId}/notes`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes }),
  });
  if (!res.ok) { showToast(errorText(await readJson(res), res)); return; }
  if (state.activeMeeting?.id === meetingId) state.activeMeeting.user_notes = notes;
  showToast(t('rdSasSavedToNotes').replace('{title}', () => m.title || ''));
}

// --- The conversation ---
const ASSIST_SUGGEST = ['rdSasSuggest1', 'rdSasSuggest2', 'rdSasSuggest3', 'rdSasSuggest4', 'rdSasSuggest5', 'rdSasSuggest6'];
const ASSIST_FOLLOW = ['rdSasFollow1', 'rdSasFollow2', 'rdSasFollow3', 'rdSasFollow4'];

function assistantSuggest(i) {
  const key = ASSIST_SUGGEST[i];
  if (key === 'rdSasSuggest4') { makeDigest(30); return; }
  if (key === 'rdSasSuggest6') {
    const input = document.getElementById('library-question');
    input.value = t('rdSasSuggest6Start');
    assistantSyncSend();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    return;
  }
  assistantSend(t(key));
}

function assistantFollow(i) {
  assistantSend(t(ASSIST_FOLLOW[i]));
}

function renderAssistantEmpty() {
  const total = state.meetings.length;
  const hours = state.meetings.reduce((a, m) => a + (Number(m.duration) || 0), 0) / 3600;
  const amount = hours >= 1 ? `${hours >= 10 ? Math.round(hours) : hours.toFixed(1)} ${t('unitH')}` : `${Math.max(1, Math.round(hours * 60))} ${t('unitMin')}`;
  const lib = t('rdSasLibrarySize').replace('{n}', () => total).replace('{amount}', () => amount);
  return `<div class="as-empty">
    <h3 class="as-empty-title">${escapeHtml(t('rdSasEmptyTitle'))}</h3>
    <p class="as-empty-sub">${escapeHtml(t('rdSasEmptySub'))} · ${escapeHtml(lib)}</p>
    <div class="as-cards">${ASSIST_SUGGEST.map((k, i) => `
      <button data-click="assistantSuggest" data-click-args='[${i}]' class="as-card">
        <span class="as-card-title">${escapeHtml(t(k))}</span><span class="as-card-sub">${escapeHtml(t(`${k}Hint`))}</span>
      </button>`).join('')}</div>
  </div>`;
}

function renderAssistantItem(item, isLast) {
  const q = `<div class="as-q">
      <div class="as-q-bubble">${escapeHtml(item.question)}</div>
      <div class="as-q-meta">${escapeHtml(item.scopeLabel || '')} · <button data-click="assistantEditQuestion" data-click-args='[${item.id}]' class="as-link">${escapeHtml(t('rdSasEdit'))}</button></div>
    </div>`;
  let body;
  if (item.status === 'pending') {
    body = `<div class="as-a"><div class="as-wait"><span class="as-wait-dot"></span>${escapeHtml(t('rdSasSearching'))}</div></div>`;
  } else if (item.status === 'stopped' || item.status === 'error') {
    body = `<div class="as-a"><div class="text-xs ${item.status === 'error' ? 'text-apple-red' : 'text-apple-secondary'}">${escapeHtml(item.status === 'error' ? item.error : t('rdSasStopped'))}</div>
      <div class="as-actions"><button data-click="assistantRetry" data-click-args='[${item.id}]' class="as-act">${escapeHtml(t('rdSasAskAgain'))}</button></div></div>`;
  } else {
    const data = item.data || {};
    if (!data.found) {
      body = `<div class="as-a"><div class="text-xs text-apple-secondary">${escapeHtml(t('libraryAskNothing'))}</div></div>`;
    } else {
      const cited = new Set([...(data.answer || '').matchAll(/\[(\d{1,3})\]/g)].map(x => Number(x[1])));
      const shown = (data.sources || []).filter(s => !cited.size || cited.has(s.n)).slice(0, 20);
      const recs = new Set(shown.map(s => s.meeting_id)).size;
      const sources = shown.map(s => `
        <button data-click="openLibrarySource" data-click-args='[${item.id}, ${s.n}]' class="as-src">
          <span class="lib-cite">${s.n}</span><span class="as-src-title">${escapeHtml(s.title)}</span>
          <span class="as-src-time">${escapeHtml(formatTime(s.start))}</span>
          <span class="as-src-quote">«${escapeHtml(s.text)}»</span>
        </button>`).join('');
      const model = data.model ? `${data.cloud ? '☁ ' : ''}${escapeHtml(data.cloud ? data.model : formatModelName(data.model))}` : '';
      body = `<div class="as-a">
        <div class="markdown-body text-[13.5px]" data-as-answer="${item.id}"></div>
        ${sources ? `<details class="as-sources"><summary>${escapeHtml(t('rdSasSourcesCount').replace('{r}', () => recs).replace('{n}', () => shown.length))}</summary>${sources}</details>` : ''}
        <div class="as-actions">
          <button data-click="assistantCopy" data-click-args='[${item.id}, {"$": "el"}]' class="as-act">${escapeHtml(t('rdSasCopy'))}</button>
          <button data-click="assistantSaveMenu" data-click-args='[{"$": "event"}, ${item.id}]' class="as-act">${escapeHtml(t('rdSasSave'))}</button>
          <button data-click="assistantRetry" data-click-args='[${item.id}]' class="as-act">${escapeHtml(t('rdSasRegenerate'))}</button>
          <span class="flex-1"></span>
          <span class="as-model">${model}${data.excluded_local ? ' · ' + escapeHtml(t('askExcludedLocal').replace('{n}', () => data.excluded_local)) : ''}</span>
        </div>
      </div>`;
    }
  }
  const follow = isLast && item.status === 'done' && item.data?.found
    ? `<div class="as-follow"><span>${escapeHtml(t('rdSasAskNext'))}</span>${ASSIST_FOLLOW.map((k, i) => `<button data-click="assistantFollow" data-click-args='[${i}]' class="as-chip-q">${escapeHtml(t(k))}</button>`).join('')}</div>` : '';
  return q + body + follow;
}

function renderAskThread() {
  // a digest or a brief made by library.js lands in the conversation shown
  if (typeof askThread !== 'undefined' && askThread.length) {
    let chat = currentChat();
    if (!chat) { chat = newChatObject(); ASSIST.chats.unshift(chat); ASSIST.chatId = chat.id; }
    for (const item of [...askThread].reverse()) {
      if (chat.items.includes(item)) continue;
      item.at = item.at || new Date().toISOString();
      chat.items.push(item);
      if (!chat.title) chat.title = item.question.slice(0, 80);
      chat.updated = item.at;
    }
    askThread.length = 0;
    // the digest itself fills the same object later: save once it has an answer
    chat.items.forEach(i => { if (i.status !== 'pending' && !i._saved) { i._saved = true; assistantSaveSoon(); } });
  }
  renderAssistant();
}

function renderAssistant(scrollDown) {
  if (!ASSIST.loaded) { assistantLoad(); return; }
  renderChatList();
  renderAssistantHead();
  renderAskScope();
  const box = document.getElementById('ask-thread');
  if (!box) return;
  const chat = currentChat();
  if (!chat || !chat.items.length) {
    box.innerHTML = renderAssistantEmpty();
    return;
  }
  box.innerHTML = chat.items.map((it, i) => renderAssistantItem(it, i === chat.items.length - 1)).join('');
  // the answers: sanitized markdown, then "[2]" becomes a link with its quote
  for (const item of chat.items) {
    const el = box.querySelector(`[data-as-answer="${item.id}"]`);
    if (!el) continue;
    el.innerHTML = sanitizedMarkdown(item.data?.answer || '');
    const byN = Object.fromEntries((item.data?.sources || []).map(s => [s.n, s]));
    linkifyCitations(el, byN, item.id);
    el.querySelectorAll('.lib-cite').forEach(b => {
      const s = byN[b.textContent];
      if (s) b.title = `${s.title} · ${formatTime(s.start)}\n«${s.text}»`;
    });
  }
  if (scrollDown) {
    const scroller = document.getElementById('library-answer');
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }
}

// Ctrl+N in the assistant: a new conversation
document.addEventListener('keydown', e => {
  if (state.appView !== 'ask' || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'n' || e.altKey || e.shiftKey) return;
  e.preventDefault();
  assistantNewChat();
});
