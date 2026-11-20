import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp, HELPERS } from './harness.mjs';
import { seedSettings } from './util.mjs';

const seed = (grade, result) => `${seedSettings(grade)} (function(){ ${HELPERS}; seedNote('n_1', ${result}); })();`;

test('playing follows tts line events and next restarts from that step', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb(), tts: makeTts() })", setup: seed('중3', 'SOLUTION'), hash: '#/board/n_1/0' });
  const { page } = app;
  try {
    await page.getByRole('button', { name: '재생', exact: true }).click();
    await page.locator('.caption').getByText('식을 봐요').waitFor();
    await page.evaluate(() => window.__ttsLine(2));
    await page.locator('.board-pos').getByText('3/4').waitFor();
    assert.equal(await page.locator('.bstep.current').count(), 1);
    assert.ok((await page.locator('.bstep.current').innerText()).length > 0);
    await page.getByRole('button', { name: '다음 단계' }).click();
    const spoken = await page.evaluate(() => window.__tts.spoken);
    assert.equal(spoken.at(-1).lines[0].id, '3');
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('without tts or speechSynthesis the board still plays to the end', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed('중3', 'SOLUTION') + ' delete window.speechSynthesis; delete window.SpeechSynthesisUtterance;', hash: '#/board/n_1/2' });
  const { page } = app;
  try {
    await page.getByRole('button', { name: '재생', exact: true }).click();
    await page.getByRole('button', { name: '재생', exact: true }).waitFor({ timeout: 15000 });
    assert.ok(await page.locator('.board-pos').getByText('1/1').isVisible());
  } finally { await app.close(); }
});

test('column visual steps show borrow marks and results', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb(), tts: makeTts() })", setup: seed('초3', 'KID_SOLUTION'), hash: '#/board/n_1/0' });
  const { page } = app;
  try {
    await page.getByRole('button', { name: '다음 단계' }).click();
    await page.locator('.bstep.current .col-mark').getByText('4', { exact: true }).waitFor();
    assert.ok(await page.locator('.bstep.current .col-result').getByText('6', { exact: true }).isVisible());
    assert.equal(await page.locator('.bstep.current .col.focus').count(), 1);
  } finally { await app.close(); }
});
