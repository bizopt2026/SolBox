import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp } from './harness.mjs';

const ACCOUNT = "({ user: makeUser('u1'), db: makeDb() })";

test('first open asks for the grade, then remembers it', async () => {
  const app = await openApp({ mocks: ACCOUNT });
  const { page } = app;
  try {
    await page.getByRole('heading', { name: '몇 학년이에요?' }).waitFor();
    await page.getByRole('button', { name: '초3', exact: true }).click();
    await page.getByRole('button', { name: /시작하기/ }).click();
    await page.locator('nav.tabbar').getByText('다시 풀기').waitFor();
    await page.reload();
    await page.locator('nav.tabbar').getByText('다시 풀기').waitFor();
    assert.ok(!(await page.getByRole('heading', { name: '몇 학년이에요?' }).isVisible()));
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('changing grade in settings switches wording', async () => {
  const app = await openApp({ mocks: ACCOUNT });
  const { page } = app;
  try {
    await page.getByRole('button', { name: '초3', exact: true }).click();
    await page.getByRole('button', { name: /시작하기/ }).click();
    await page.locator('nav.tabbar').getByText('나').click();
    await page.getByRole('button', { name: '중2', exact: true }).click();
    await page.getByText('계정에 저장').waitFor();
    await page.locator('nav.tabbar').getByText('노트').click();
    await page.locator('nav.tabbar').getByText('복습').waitFor();
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('without window.claude the app keeps notes on this device', async () => {
  const app = await openApp({ mocks: null });
  const { page } = app;
  try {
    await page.getByRole('button', { name: '중1', exact: true }).click();
    await page.getByRole('button', { name: /시작하기/ }).click();
    await page.getByText('이 기기에만 저장됨').waitFor();
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});
