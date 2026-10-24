# Redesign: the shell and the packages

The new design (the mockups of the session: top bar, home, tasks, studio, assistant, settings) is
built in parts. The shell is done first; five packages then work at the same time, each in its own
files, so their changes merge without conflicts.

## The shell (done)

- **Top bar** (`static/parts/workspace.html`, `<nav id="app-bar" class="topbar">`): the red
  «Запись» (`shellQuickRecord`: records at once with the last studio settings and stays in the view,
  «Стоп hh:mm:ss» while recording, a popover «Запись сохранена» after it), «Главная», «Задачи N»,
  the open recording (`#tb-record`: name, «Расшифровка», «✦ Анализ N», «Участники N», ⋯ menu),
  «Студия», «✦ Ассистент». The AI mark is `<svg class="tb-ai">` filled with `url(#vs-ai-grad)`.
- **Status bar** (`static/parts/statusbar.html`, `renderStatusbar`): CPU, GPU, RAM, the queue.
- **Interface scale** (`ui_scale`, 75 % by default): CSS zoom on `<html>`. A point of the window
  (`clientX/Y`, `getBoundingClientRect()`) is divided by the zoom before it becomes
  `style.left/top`: use `zpx(v)`, `viewW()`, `viewH()` (core.js). `100vh` is not zoomed: the body
  height is `calc(100vh / var(--ui-zoom))`.
- **Parts may include parts** (`app/page.py`, up to 3 levels): a view of the main area lives in its
  own part included from `workspace.html`.

## Router (static/js/shell.js)

- Views: `APP_VIEWS = ['home', 'library', 'tasks', 'studio', 'ask', 'settings']`; each is
  `#<name>-view` in the main area. `library` is the open recording (the name stays for the many
  places that open one). Ctrl+1…5: home, recording, tasks, studio, assistant. Ctrl+I: details.
- `switchAppView(name)` shows a view. `openRecordTab('transcript' | 'summary' | 'speakers' | 'info')`
  opens a tab of the open recording (`switchWorkspaceTab` underneath).
- `registerView(name, { show, hide, refresh })`: `show()` each time the view comes on screen,
  `hide()` when it leaves, `refresh()` when the list of recordings was reloaded while it is shown
  (`onLibraryChanged`, called by `loadMeetingsList`).
- `renderTopbar()` follows the open recording (also on a 1.5 s timer); `badge(id, n)` sets a count.
- Settings: `openModal('settings', tab)` moves `#modal-settings` into `#settings-view` and shows it
  as a page; `closeModal('settings')` goes back to the previous view (`state.prevView`).
  `anyModalOpen()` does not count the settings page.
- `stopAndTranscribeRecording({ quiet: true })` returns the first new recording (or `null`) and
  shows no toast; `state.quickRecStay` keeps `startLiveRecording` from switching to the studio.

## Where each package writes

Every package adds its UI strings to `static/i18n.js` **under its marker comment**
(`// --- redesign: … ---`, in both the `ru` and the `en` object; the keys start with its prefix), and its styles in
`static/styles.css` **under its marker comment**. Russian server messages get English in
`static/server_text.js` (in its alphabetical place, not at the end). New Tailwind classes: `npm run vendor`.

| Package | Owns | i18n marker | Notes |
|---|---|---|---|
| A — editor and player | `static/js/redesign_player.js`, `static/js/player.js`, `static/js/app.js` (player and transcript parts), `#player-footer` and `#wview-transcript` in `parts/workspace.html`, `app/media_cut.py`, `app/api/media.py` | `rdPlyr…` | wave, «Звук», skip pauses, A–B, marks, «Без запинок» view-only, saved copies, record details window |
| B — home, import, sidebar, status bar | `static/js/home.js`, `static/js/importdlg.js`, `parts/home.html`, `parts/import.html`, `parts/sidebar.html`, `parts/statusbar.html`, `static/js/library.js` (sidebar list) | `rdHm…` | home page, import window with processing, today's meetings, auto-record of all meetings |
| C — analysis, speakers, people | `static/js/people.js`, `#wview-summary` and `#wview-speakers` in `parts/workspace.html`, analysis parts of `static/js/app.js`, `app/api/analysis.py` | `rdAs…` | analysis sections and overview, speaker cards, «Все люди» directory |
| D — tasks | `static/js/tasks.js`, `parts/tasks.html`, `app/commitments.py` | `rdTk…` | list, people filter, assign, calendar, summary panel, «+ Задача» |
| E — studio, assistant, settings | `static/js/studio.js`, `static/js/settings.js`, `parts/settings.html`, studio and ask views in `parts/workspace.html` | `rdSas…` | studio redesign, assistant with sources, settings as a page with sections |

Files of the shell (`shell.js`, the top bar, `core.js`, `index.html`) change only through the
integration. A package that needs something from the shell says so in its report.

## Checks before a package is done

`PYTHONPATH=tests/stubs python -m unittest discover -s tests -t .` (exit code 0),
`python -m compileall -q app run.py build_exe.py tests`, the browser smoke test of
`.github/workflows/ci.yml` (`node tests/ui/smoke.mjs` against `uvicorn app.main:app --port 8799`
with temporary `VERBASCRIBE_DATA_DIR`/`VERBASCRIBE_MODELS_DIR`), and screenshots of the new screens.
