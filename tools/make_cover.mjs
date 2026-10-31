// The README covers docs/cover-en.png and docs/cover-ru.png (1280×640), drawn in Chromium from the app's own
// logo, fonts and colours:   node tools/make_cover.mjs   (Playwright: PLAYWRIGHT_MODULE, CHROMIUM as in tests/ui)
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pw = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const font = f => `data:font/woff2;base64,${readFileSync(path.join(root, 'static/vendor/fonts', f)).toString('base64')}`;

const TEXT = {
  en: {
    tagline: 'Records, transcribes and analyzes meetings — on your computer',
    lines: [['00:12', 'Anna', '#64B5FF', 'Let’s start with what we finished this week'],
            ['00:31', 'Boris', '#FFB340', 'The report is almost ready, I’ll send it on Friday']],
    task: 'Task', taskText: 'Send the report by Friday', who: 'Boris',
    footer: 'Windows 10 / 11 · speech models on your PC · free, source on GitHub',
  },
  ru: {
    tagline: 'Записывает, расшифровывает и анализирует встречи — на вашем компьютере',
    lines: [['00:12', 'Анна', '#64B5FF', 'Давайте начнём с того, что успели за неделю'],
            ['00:31', 'Борис', '#FFB340', 'Отчёт почти готов, пришлю его в пятницу']],
    task: 'Задача', taskText: 'Прислать отчёт к пятнице', who: 'Борис',
    footer: 'Windows 10 / 11 · модели речи на вашем ПК · бесплатно, код на GitHub',
  },
};

// A waveform of fixed bars (no randomness: the image is the same on every run), coloured by speaker
function wave() {
  const bars = [];
  for (let i = 0; i < 64; i++) {
    const h = 6 + Math.round(26 * Math.abs(Math.sin(i * 0.55) * Math.cos(i * 0.21)) + 6 * Math.abs(Math.sin(i * 1.7)));
    const color = i < 30 ? '#64B5FF' : i < 34 ? 'rgba(255,255,255,0.18)' : '#FFB340';
    bars.push(`<i style="height:${h}px;background:${color}"></i>`);
  }
  return bars.join('');
}

// Willow fluff drifting around the logo, at fixed places
const FLUFF = [[180, 120, 5], [96, 210, 3], [470, 96, 4], [430, 520, 3], [70, 470, 4], [260, 560, 3], [1180, 90, 3],
  [1220, 560, 4], [700, 600, 3], [1010, 40, 3], [560, 70, 2], [140, 330, 2], [480, 300, 2]];

function html(t) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: Inter; font-weight: 100 900; src: url(${font('inter-latin-wght-normal.woff2')}) format('woff2'); unicode-range: U+0000-00FF, U+2000-206F, U+2190-21FF; }
@font-face { font-family: Inter; font-weight: 100 900; src: url(${font('inter-latin-ext-wght-normal.woff2')}) format('woff2'); unicode-range: U+0100-024F; }
@font-face { font-family: Inter; font-weight: 100 900; src: url(${font('inter-cyrillic-wght-normal.woff2')}) format('woff2'); unicode-range: U+0400-045F, U+0490-0491; }
@font-face { font-family: 'JetBrains Mono'; font-weight: 100 900; src: url(${font('jetbrains-mono-latin-wght-normal.woff2')}) format('woff2'); }
* { box-sizing: border-box; margin: 0; }
body { width: 1280px; height: 640px; overflow: hidden; font-family: Inter, sans-serif; color: #F5F5F7;
  background: radial-gradient(ellipse 70% 90% at 22% 45%, #16405c 0%, #0c2232 45%, #07131d 100%); position: relative; }
.rings { position: absolute; left: 270px; top: 320px; }
.rings b { position: absolute; border: 1.5px solid rgba(74, 222, 128, 0.07); border-radius: 50%; transform: translate(-50%, -50%); }
.fluff { position: absolute; border-radius: 50%; background: rgba(248, 250, 252, 0.55); filter: blur(0.4px); }
.logo { position: absolute; left: 112px; top: 162px; width: 316px; height: 316px; border-radius: 76px;
  background: radial-gradient(circle at 50% 30%, #1d4a68 0%, #0E2233 72%); border: 1.5px solid rgba(255,255,255,0.12);
  box-shadow: 0 30px 80px rgba(0,0,0,0.55), inset 0 2px 0 rgba(255,255,255,0.07); display: flex; align-items: center; justify-content: center; }
.logo svg { width: 210px; height: 210px; }
.right { position: absolute; left: 520px; top: 92px; width: 700px; }
h1 { font-size: 84px; font-weight: 700; letter-spacing: -2px; line-height: 1; }
.tag { margin-top: 18px; font-size: 25px; color: #C9D6E2; line-height: 1.35; }
.card { margin-top: 34px; width: 640px; padding: 18px 20px 16px; border-radius: 16px; background: rgba(16, 17, 21, 0.62);
  border: 1px solid rgba(255,255,255,0.11); box-shadow: 0 18px 50px rgba(0,0,0,0.4); }
.wave { display: flex; align-items: center; justify-content: space-between; height: 40px; margin-bottom: 12px; }
.wave i { display: block; width: 6px; border-radius: 3px; opacity: 0.9; }
.line { display: flex; gap: 10px; font-size: 18px; line-height: 1.5; color: #E5E5EA; white-space: nowrap; }
.line + .line { margin-top: 4px; }
.ts { font-family: 'JetBrains Mono', monospace; font-size: 15px; color: #8A8A90; padding-top: 2px; }
.who { font-weight: 600; }
.task { margin-top: 14px; display: inline-flex; align-items: center; gap: 10px; padding: 8px 14px; border-radius: 10px;
  background: rgba(10, 132, 255, 0.16); border: 1px solid rgba(100, 181, 255, 0.35); font-size: 17px; }
.task .k { color: #64B5FF; font-weight: 600; }
.task .p { color: #8A8A90; }
.foot { position: absolute; left: 520px; bottom: 44px; font-size: 19px; color: #9FB0C0; }
</style></head><body>
<div class="rings">${[380, 520, 660, 800].map(d => `<b style="width:${d}px;height:${d}px"></b>`).join('')}</div>
${FLUFF.map(([x, y, r]) => `<span class="fluff" style="left:${x}px;top:${y}px;width:${2 * r}px;height:${2 * r}px"></span>`).join('')}
<div class="logo"><svg viewBox="0 0 24 24" fill="none">
  <path d="M5 20C8.5 15.5 13 10.5 18.5 4.5" stroke="#4ADE80" stroke-width="1.8" stroke-linecap="round"/>
  <ellipse cx="8.2" cy="13.2" rx="2.1" ry="3.1" transform="rotate(-28 8.2 13.2)" fill="#F8FAFC"/>
  <ellipse cx="14.2" cy="12.8" rx="2.0" ry="3.0" transform="rotate(32 14.2 12.8)" fill="#F8FAFC"/>
  <ellipse cx="12.0" cy="7.5" rx="2.0" ry="3.0" transform="rotate(-22 12.0 7.5)" fill="#F8FAFC"/>
  <ellipse cx="18.2" cy="5.2" rx="1.7" ry="2.5" transform="rotate(25 18.2 5.2)" fill="#F8FAFC"/>
</svg></div>
<div class="right">
  <h1>VerbaScribe</h1>
  <p class="tag">${t.tagline}</p>
  <div class="card">
    <div class="wave">${wave()}</div>
    ${t.lines.map(([ts, who, c, text]) => `<p class="line"><span class="ts">${ts}</span><span class="who" style="color:${c}">${who}</span><span>${text}</span></p>`).join('')}
    <div class="task"><span class="k">✦ ${t.task}</span><span>${t.taskText}</span><span class="p">· ${t.who}</span></div>
  </div>
</div>
<p class="foot">${t.footer}</p>
</body></html>`;
}

const browser = await pw.chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1280, height: 640 } });
for (const lang of Object.keys(TEXT)) {
  await page.setContent(html(TEXT[lang]));
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(root, 'docs', `cover-${lang}.png`) });
  console.log(`docs/cover-${lang}.png`);
}
await browser.close();
