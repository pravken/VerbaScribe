// Copies pinned front-end libraries and fonts into static/vendor and compiles Tailwind CSS.
// Usage: npm install && npm run vendor
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vendor = join(root, 'static', 'vendor');
const fonts = join(vendor, 'fonts');
mkdirSync(fonts, { recursive: true });

const copies = [
  ['node_modules/marked/marked.min.js', 'marked.min.js'],
  ['node_modules/dompurify/dist/purify.min.js', 'purify.min.js'],
  ['node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2', 'fonts/inter-latin-wght-normal.woff2'],
  ['node_modules/@fontsource-variable/inter/files/inter-cyrillic-wght-normal.woff2', 'fonts/inter-cyrillic-wght-normal.woff2'],
  ['node_modules/@fontsource-variable/inter/files/inter-latin-ext-wght-normal.woff2', 'fonts/inter-latin-ext-wght-normal.woff2'],
  ['node_modules/@fontsource-variable/inter/files/inter-cyrillic-ext-wght-normal.woff2', 'fonts/inter-cyrillic-ext-wght-normal.woff2'],
  ['node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2', 'fonts/jetbrains-mono-latin-wght-normal.woff2'],
  ['node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-cyrillic-wght-normal.woff2', 'fonts/jetbrains-mono-cyrillic-wght-normal.woff2'],
  ['node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-ext-wght-normal.woff2', 'fonts/jetbrains-mono-latin-ext-wght-normal.woff2'],
];
for (const [src, dst] of copies) {
  copyFileSync(join(root, src), join(vendor, dst));
  console.log(`copied ${dst}`);
}

const tailwindBin = join(root, 'node_modules', 'tailwindcss', 'lib', 'cli.js');
execFileSync(process.execPath, [
  tailwindBin,
  '-c', join(root, 'tailwind.config.js'),
  '-i', join(root, 'tools', 'tailwind.input.css'),
  '-o', join(vendor, 'tailwind.css'),
  '--minify',
], { stdio: 'inherit' });
