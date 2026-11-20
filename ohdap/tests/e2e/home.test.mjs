import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp, HELPERS } from './harness.mjs';
import { seedSettings } from './util.mjs';

const three = `seedNote('n_1', SOLUTION, { problem: '근의 공식 수식 $x^2$ 문제', createdAt: '2026-11-18T00:00:00Z' });
seedNote('n_2', SOLUTION, { problem: '삼각비 문제', unit: '삼각비', createdAt: '2026-11-19T00:00:00Z' });
seedNote('n_3', SOLUTION, { problem: 'what that 문제', subject: '영어', unit: '관계대명사', createdAt: '2026-11-20T00:00:00Z' });`;
const seed = (grade, body) => `${seedSettings(grade)} (function(){ ${HELPERS}; ${body} })();`;

test('subject chips and search filter the list', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed('중3', three), hash: '#/home' });
  const { page } = app;
  try {
    await page.locator('.note-item').first().waitFor();
    assert.equal(await page.locator('.note-item').count(), 3);
    assert.equal(await page.locator('.note-item').filter({ hasText: '수식' }).locator('.katex').count(), 1, 'math in list titles is rendered');
    await page.getByRole('button', { name: '영어', exact: true }).click();
    assert.equal(await page.locator('.note-item').count(), 1);
    await page.getByRole('button', { name: '전체', exact: true }).click();
    await page.getByLabel('검색').fill('삼각');
    assert.equal(await page.locator('.note-item').count(), 1);
    assert.ok((await page.locator('.note-item').innerText()).includes('삼각비'));
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('device notes can be moved into the account', async () => {
  const device = `localStorage.setItem('ohdap:v1', JSON.stringify({ notes: { n_d: { type: 'note', id: 'n_d', createdAt: '2026-11-20T01:00:00Z', subject: '과학', unit: '전기', problem: '옴의 법칙 문제', choices: [], result: null, status: 'failed', stage: 0, due: '2099-01-01', starred: false } }, drills: {}, settings: { type: 'settings', grade: '중3', onboarded: true, stars: 0 } }));`;
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed('중3', three) + device, hash: '#/home' });
  const { page } = app;
  try {
    await page.getByText('이 기기에 노트 1개가 있어요').waitFor();
    await page.getByRole('button', { name: '계정으로 옮기기' }).click();
    await page.locator('.note-item').getByText('옴의 법칙').waitFor();
    assert.equal(await page.getByText('이 기기에 노트 1개가 있어요').count(), 0);
    assert.equal(await page.locator('.note-item').count(), 4);
  } finally { await app.close(); }
});

test('export saves a markdown file', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb(), downloads: makeDownloads() })", setup: seed('중3', three), hash: '#/settings' });
  const { page } = app;
  try {
    await page.getByRole('button', { name: '노트 내보내기' }).click();
    await page.waitForFunction(() => window.__downloads.length === 1);
    const d = await page.evaluate(() => window.__downloads[0]);
    assert.ok(d.filename.startsWith('오답노트-') && d.filename.endsWith('.md'));
  } finally { await app.close(); }
});

test('concepts page groups notes by concept', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed('중3', three), hash: '#/concepts' });
  try {
    await app.page.getByText('근의 공식').first().waitFor();
    assert.ok((await app.page.locator('.concept-group').first().innerText()).includes('3'));
  } finally { await app.close(); }
});

test('kid home shows stars and the big photo button; empty state guides', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed('초3', ''), hash: '#/home' });
  try {
    await app.page.getByRole('link', { name: /틀린 문제 찍기/ }).waitFor();
    assert.ok(await app.page.getByText('별 0개').isVisible());
    assert.ok(await app.page.getByText('아직 노트가 없어요').isVisible());
  } finally { await app.close(); }
});
