// Bundles src/ modules + KaTeX into one self-contained index.html.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const rd = (p) => readFileSync(join(ROOT, p), 'utf8');

// Bundle order: dependencies first. Every top-level name must be unique across files.
export const FILES = [
  'src/grades.js',
  'src/review.js',
  'src/answer.js',
  'src/mathtext.js',
  'src/column.js',
  'src/ai.js',
  'src/store.js',
  'src/concepts.js',
  'src/speaker.js',
  'src/caps.js',
  'src/ui/dom.js',
  'src/ui/router.js',
  'src/ui/chrome.js',
  'src/ui/screens/onboard.js',
  'src/ui/screens/settings.js',
  'src/ui/screens/home.js',
  'src/image.js',
  'src/ui/aimsg.js',
  'src/ui/screens/upload.js',
  'src/ui/screens/confirm.js',
  'src/ui/columnview.js',
  'src/ui/solve.js',
  'src/ui/screens/note.js',
  'src/ui/screens/board.js',
  'src/ui/screens/review.js',
  'src/ui/screens/drill.js',
  'src/main.js'
];

function stripModule(src) {
  return src
    .replace(/^import\s[\s\S]*?from\s+['"][^'"]+['"];?\s*$/gm, '')
    .replace(/^export\s+default\s+/gm, '')
    .replace(/^export\s+(?=(async\s+)?function|const|let|class)/gm, '');
}

function katexCss() {
  const dist = 'node_modules/katex/dist/';
  let css = rd(dist + 'katex.min.css');
  // keep only woff2 sources, inlined
  css = css.replace(/src:([^;}]*)/g, (m, list) => {
    const w = list.match(/url\(fonts\/([^)]+\.woff2)\)/);
    if (!w) return m;
    const b64 = readFileSync(join(ROOT, dist, 'fonts', w[1])).toString('base64');
    return `src:url(data:font/woff2;base64,${b64}) format("woff2")`;
  });
  return css;
}

export function build() {
  const app = FILES.map((f) => `// ---- ${f}\n` + stripModule(rd(f))).join('\n');
  const js = `(function(){'use strict';\n${app}\n})();`;
  const html = rd('src/index.template.html')
    .replace('<!--KATEX_CSS-->', () => `<style>${katexCss()}</style>`)
    .replace('<!--KATEX_JS-->', () => `<script>${rd('node_modules/katex/dist/katex.min.js')}</script>`)
    .replace('<!--APP_JS-->', () => `<script id="app-js">${js}</script>`);
  writeFileSync(join(ROOT, 'index.html'), html);
  return html.length;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const n = build();
  console.log(`index.html ${(n / 1024).toFixed(0)} KiB`);
}
