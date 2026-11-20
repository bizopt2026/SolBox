import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp, HELPERS } from './harness.mjs';
import { seedSettings, nav } from './util.mjs';

const seed = (grade) => `${seedSettings(grade)} (function(){ ${HELPERS}; seedNote('n_1', ${grade.startsWith('초') ? 'KID_SOLUTION' : 'SOLUTION'}, { myAnswer: '3', due: '2000-01-01' }); })();`;
const ROUTES = ['#/home', '#/upload', '#/note/n_1', '#/board/n_1/0', '#/review', '#/drill/n_1', '#/settings', '#/concepts'];

for (const grade of ['중3', '초3']) {
  test(`no horizontal scroll at 390px (${grade})`, async () => {
    const app = await openApp({ mocks: `({ user: makeUser('u1'), db: makeDb(), sample: makeSample({ drill: { problem: '문제', choices: [], answer: '2', answer_short: '2', steps: [{ board: 'a', say: 'b' }] } }) })`, setup: seed(grade), hash: '#/home' });
    const { page } = app;
    try {
      for (const r of ROUTES) {
        await nav(page, r);
        await page.waitForTimeout(400);
        const sw = await page.evaluate(() => document.documentElement.scrollWidth);
        assert.ok(sw <= 390, `${r} scrollWidth ${sw}`);
      }
      assert.deepEqual(app.errors, []);
    } finally { await app.close(); }
  });
}
