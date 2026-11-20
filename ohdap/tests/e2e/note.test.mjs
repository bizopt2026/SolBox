import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp } from './harness.mjs';
import { pngBuffer, seedSettings, pickPhoto } from './util.mjs';

const mocks = (solve, extra = 'assets: makeAssets(),') => `({ user: makeUser('u1'), db: makeDb(), ${extra} sample: makeSample({ read: READ_ONE, solve: ${solve} }) })`;

async function toNote(page) {
  await page.goto(page.url().split('#')[0] + '#/upload');
  await pickPhoto(page, await pngBuffer(page, 800, 600));
  await page.getByRole('button', { name: 'AI로 정리하기' }).click();
  await page.getByRole('button', { name: '맞아요, 풀어 주세요' }).click();
}

const notes = (page) => page.evaluate(() => [...window.__db.entries()].filter(([, d]) => d.type === 'note').map(([, d]) => d));

test('solving saves a note and shows answer, tabs, and concepts', async () => {
  const app = await openApp({ mocks: mocks('SOLUTION'), setup: seedSettings('중3') });
  const { page } = app;
  try {
    await toNote(page);
    await page.locator('.answer-card.ok').waitFor();
    assert.ok((await page.locator('.answer-card.ok').innerText()).includes('−1'));
    assert.equal(await page.getByRole('tab').count(), 3);
    await page.getByRole('tab', { name: '근의 공식' }).click();
    assert.ok(await page.locator('.steps').getByText('공식').isVisible());
    assert.ok(await page.getByRole('heading', { name: '핵심 개념' }).isVisible());
    assert.ok(await page.getByRole('link', { name: /판서로 풀이 보기/ }).isVisible());
    const saved = await notes(page);
    assert.equal(saved.length, 1);
    assert.equal(saved[0].stage, 0);
    assert.equal(saved[0].status, 'ready');
    assert.equal(saved[0].photoId, 'a0');
    const solveCall = (await page.evaluate(() => window.__sampleCalls)).find((c) => c.task === 'solve');
    assert.equal(solveCall.images.length, 1);
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('kid wording on the note screen', async () => {
  const app = await openApp({ mocks: mocks('SOLUTION'), setup: seedSettings('초3') });
  try {
    await toNote(app.page);
    await app.page.getByRole('heading', { name: '꼭 기억해요' }).waitFor();
    assert.ok(await app.page.getByRole('link', { name: /선생님 설명 듣기/ }).isVisible());
  } finally { await app.close(); }
});

test('bad AI output saves a failed note that can be re-solved', async () => {
  const app = await openApp({ mocks: mocks('[{}, {}, SOLUTION]'), setup: seedSettings('중3') });
  const { page } = app;
  try {
    await toNote(page);
    await page.getByRole('button', { name: '다시 정리' }).waitFor();
    assert.equal((await notes(page))[0].status, 'failed');
    await page.getByRole('button', { name: '다시 정리' }).click();
    await page.locator('.answer-card.ok').waitFor();
    assert.equal((await notes(page))[0].status, 'ready');
  } finally { await app.close(); }
});

test('without assets the note is saved without a photo', async () => {
  const app = await openApp({ mocks: mocks('SOLUTION', ''), setup: seedSettings('중3') });
  try {
    await toNote(app.page);
    await app.page.locator('.answer-card.ok').waitFor();
    assert.equal((await notes(app.page))[0].photoId, null);
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});
