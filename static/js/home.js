// VerbaScribe page script: the home page (package B, docs/redesign-plan.md). A first version: the
// date, the greeting and the latest recordings; the redesign adds meetings of the day and tasks.
// The parts are classic scripts sharing one global scope, loaded in order by index.html.

function renderHome() {
  const now = new Date();
  const locale = state.uiLang === 'en' ? 'en-GB' : 'ru-RU';
  document.getElementById('home-date').textContent = now.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long' });
  const box = document.getElementById('home-recent');
  const list = [...(state.meetings || [])].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))).slice(0, 8);
  box.innerHTML = list.length ? list.map(m => `
    <button ${pageCall('click', 'openMeeting', [m.id])} class="home-row">
      <span class="flex-1 min-w-0 truncate text-[13.5px] text-white">${escapeHtml(m.title)}</span>
      <span class="text-[11.5px] font-mono text-apple-tertiary shrink-0">${escapeHtml(formatTime(m.duration || 0))}</span>
    </button>`).join('')
    : `<p class="py-6 text-center text-[12.5px] text-apple-tertiary">${escapeHtml(t('homeRecentEmpty'))}</p>`;
}

registerView('home', { show: renderHome, refresh: renderHome });
