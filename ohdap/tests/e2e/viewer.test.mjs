import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp, HELPERS } from './harness.mjs';
import { seedSettings } from './util.mjs';

// The artifact viewer never shows confirm() (it returns false) and does not pass #/path hashes.
const viewerLimits = "window.confirm = () => false; window.alert = () => {}; window.prompt = () => null;";
const seed = `${seedSettings('중3')} (function(){ ${HELPERS}; seedNote('n_1', SOLUTION); })(); ${viewerLimits}`;

test('navigation does not write #/paths into the address', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed });
  const { page } = app;
  try {
    await page.locator('.note-item').first().click();
    await page.locator('.answer-card.ok').waitFor();
    await page.locator('nav.tabbar, header').getByRole('link', { name: '뒤로' }).click();
    await page.locator('.note-item').first().waitFor();
    assert.equal(await page.evaluate(() => location.hash), '');
  } finally { await app.close(); }
});

test('deleting a note uses an in-page confirmation', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed });
  const { page } = app;
  try {
    await page.locator('.note-item').first().click();
    await page.getByRole('button', { name: '노트 지우기' }).click();
    await page.getByRole('button', { name: '지우기', exact: true }).click();
    await page.getByText('아직 노트가 없어요').waitFor();
    assert.equal(await page.evaluate(() => window.__db.has('data/users/u1/n_1')), false);
  } finally { await app.close(); }
});

// Final review fix 5
test('a slow screen does not replace a newer one', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed });
  const { page } = app;
  try {
    await page.locator('.note-item').first().waitFor();
    await page.evaluate(() => { window.__dbDelay = { 'data/users/u1/n_1': 1200 }; });
    await page.locator('.note-item').first().click();
    await page.locator('nav.tabbar').getByText('복습').click();
    await page.getByRole('heading', { name: '오늘 다시 풀 문제' }).waitFor();
    await page.waitForTimeout(1600);
    assert.equal(await page.locator('.answer-card').count(), 0);
    assert.ok(await page.getByRole('heading', { name: '오늘 다시 풀 문제' }).isVisible());
  } finally { await app.close(); }
});
