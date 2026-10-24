// Browser smoke test of the page (CI: .github/workflows/ci.yml): every view, every settings tab and the
// About window open without a JavaScript error. Needs a running server:
//   VERBASCRIBE_TOKEN=smoke python -m uvicorn app.main:app --port 8799
//   node tests/ui/smoke.mjs            (BASE=http://127.0.0.1:8799 TOKEN=smoke by default)
const pw = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BASE || 'http://127.0.0.1:8799';
const token = process.env.TOKEN || 'smoke';

const browser = await pw.chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1300, height: 860 } });
page.setDefaultTimeout(8000);
const errors = [];
page.on('pageerror', e => errors.push(`page: ${e.message}`));
// the missing favicon is reported by newer browsers without its address in the text
page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(`${m.text()} ${m.location()?.url || ''}`)) errors.push(`console: ${m.text()}`); });
page.on('response', r => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url().replace(/\?.*/, '')}`); });

const step = async (name, fn) => {
  try { await fn(); } catch (e) { errors.push(`${name}: ${e.message.split('\n')[0]}`); }
};

await page.goto(`${base}/?token=${token}`);
await page.waitForTimeout(2000);
await page.evaluate(() => document.getElementById('first-run-wizard')?.remove());
for (const view of ['studio', 'ask', 'library']) {
  await step(`view ${view}`, () => page.evaluate(v => switchAppView(v), view));
  await page.waitForTimeout(400);
}
// Handlers that use their element (event.currentTarget): the view menu is placed under its button,
// a click on a window's backdrop closes it
await step('library view menu', async () => {
  await page.evaluate(() => switchAppView('library'));
  await page.click('button[data-click="openLibViewMenu"]');
  await page.waitForTimeout(200);
  if (!(await page.isVisible('#lib-view-menu'))) throw new Error('not opened');
  await page.mouse.click(5, 5);
});
await step('backdrop closes a window', async () => {
  await page.evaluate(() => openModal('shortcuts'));
  await page.waitForTimeout(300);
  await page.mouse.click(5, 795);
  await page.waitForTimeout(200);
  if (!(await page.evaluate(() => document.getElementById('modal-shortcuts').classList.contains('hidden')))) throw new Error('still open');
});
await step('settings', () => page.evaluate(() => openModal('settings', 'general')));
for (const tab of await page.evaluate(() => SETTINGS_TABS)) {
  await step(`settings tab ${tab}`, async () => {
    await page.evaluate(t => selectSettingsTab(t), tab);
    await page.waitForTimeout(300);
    const shown = await page.evaluate(t => !document.querySelector(`[data-stab="${t}"]`).classList.contains('hidden'), tab);
    if (!shown) throw new Error('not shown');
  });
}
await step('close settings', () => page.evaluate(() => closeModal('settings')));
await step('about', async () => {
  await page.evaluate(() => openAbout());
  await page.waitForTimeout(500);
  await page.click('#about-logo');
  await page.waitForTimeout(300);
  await page.evaluate(() => closeModal('about'));
});
await step('english', () => page.evaluate(() => { setUILanguage('en'); setUILanguage('ru'); }));
await page.waitForTimeout(500);
await browser.close();

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
console.log('UI smoke test passed');
