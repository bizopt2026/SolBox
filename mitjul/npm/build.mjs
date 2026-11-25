// 밑줄 index.html 을 npm 패키지(mitjul@<판>.0.0)로 묶어요.
// 아티팩트는 npm CDN 스크립트만 불러올 수 있어서, 앱 코드를 app.js 안의 문자열로 담아요.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'index.html'), 'utf8');
const m = html.match(/const MJ_BUILD = (\d+);/);
if (!m) throw new Error('index.html 에서 MJ_BUILD 를 찾지 못했어요');
const build = Number(m[1]);
const out = join(here, 'dist');
mkdirSync(out, { recursive: true });
writeFileSync(join(out, 'app.js'), `window.__MITJUL_PKG__=${JSON.stringify({ build, html })};\n`);
writeFileSync(join(out, 'package.json'), JSON.stringify({
  name: 'mitjul',
  version: `${build}.0.0`,
  description: '밑줄 독서 앱의 배포본 (claude.ai 아티팩트 복제본이 업데이트할 때 불러와요)',
  license: 'UNLICENSED',
  files: ['app.js'],
  repository: { type: 'git', url: 'git+https://github.com/bizopt2026/bizopt2026.git', directory: 'mitjul' }
}, null, 2) + '\n');
console.log(`mitjul@${build}.0.0 (${html.length}자)`);
