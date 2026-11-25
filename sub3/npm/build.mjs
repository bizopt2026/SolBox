// 써브쓰리 app/index.html 을 npm 패키지(@gulex3/sub3@<APP_VERSION>)로 묶어요.
// 아티팩트는 npm CDN 스크립트만 불러올 수 있어서, 앱 코드를 app.js 안의 문자열로 담아요.
// 여러 버전을 한꺼번에 불러 봐도 섞이지 않게 window.__SUB3_PKGS__[버전] 에 넣어요.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'app', 'index.html'), 'utf8');
const m = html.match(/var APP_VERSION="(\d+\.\d+\.\d+)"/);
if (!m) throw new Error('app/index.html 에서 APP_VERSION 을 찾지 못했어요');
const version = m[1];
const out = join(here, 'dist');
mkdirSync(out, { recursive: true });
const json = JSON.stringify({ version, html }).replace(/</g, '\\u003c');
writeFileSync(join(out, 'app.js'), `(window.__SUB3_PKGS__=window.__SUB3_PKGS__||{})[${JSON.stringify(version)}]=${json};\n`);
writeFileSync(join(out, 'package.json'), JSON.stringify({
  name: '@gulex3/sub3',
  version,
  description: '써브쓰리 마라톤 훈련 앱의 배포본 (claude.ai 아티팩트 복제본이 업데이트할 때 불러와요)',
  license: 'UNLICENSED',
  files: ['app.js'],
  repository: { type: 'git', url: 'git+https://github.com/bizopt2026/bizopt2026.git', directory: 'sub3' }
}, null, 2) + '\n');
console.log(`@gulex3/sub3@${version} (${html.length}자)`);
