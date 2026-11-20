import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp } from './harness.mjs';
import { pngBuffer, seedSettings, pickPhoto } from './util.mjs';

const mocks = (read) => `({ user: makeUser('u1'), db: makeDb(), assets: makeAssets(), sample: makeSample({ read: ${read}, solve: SOLUTION }) })`;

async function upload(page) {
  await page.goto(page.url().split('#')[0] + '#/upload');
  const buf = await pngBuffer(page, 4000, 3000);
  await pickPhoto(page, buf);
  await page.getByRole('button', { name: 'AI로 정리하기' }).click();
}

test('photo is shrunk to jpeg and the read problem is shown with math', async () => {
  const app = await openApp({ mocks: mocks('READ_ONE'), setup: seedSettings('중3') });
  const { page } = app;
  try {
    await upload(page);
    await page.getByRole('heading', { name: '문제를 맞게 읽었나요?' }).waitFor();
    assert.ok(await page.locator('.problem-view .katex').count() > 0);
    const calls = await page.evaluate(() => window.__sampleCalls);
    assert.equal(calls[0].task, 'read');
    assert.equal(calls[0].images[0].type, 'image/jpeg');
    assert.ok(Math.max(calls[0].images[0].width, calls[0].images[0].height) <= 1600);
    assert.equal(calls[0].opts.modelTier, 'complex');
    await page.getByRole('button', { name: '고치기' }).click();
    await page.locator('textarea').fill('고친 문제');
    assert.ok(await page.getByRole('button', { name: '고친 문제로 풀기' }).isVisible());
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('unreadable photo asks to retake', async () => {
  const app = await openApp({ mocks: mocks("({ readable: false, reason: '사진이 흐려요' })"), setup: seedSettings('중3') });
  try {
    await upload(app.page);
    await app.page.getByText('사진이 흐려요').waitFor();
    assert.ok(await app.page.getByRole('button', { name: '다시 찍기' }).isVisible());
  } finally { await app.close(); }
});

test('several problems let the student pick one', async () => {
  const two = "({ readable: true, problems: [{ problem: '첫째 문제', choices: [] }, { problem: '둘째 문제', choices: ['가', '나'] }] })";
  const app = await openApp({ mocks: mocks(two), setup: seedSettings('중3') });
  const { page } = app;
  try {
    await upload(page);
    await page.getByRole('button', { name: /둘째 문제/ }).click();
    await page.getByRole('heading', { name: '문제를 맞게 읽었나요?' }).waitFor();
    assert.ok(await page.locator('.problem-view').getByText('둘째 문제').isVisible());
  } finally { await app.close(); }
});

test('rate limit shows a message and does not retry', async () => {
  const app = await openApp({ mocks: mocks("({ reject: { code: 'rate_limited', message: 'x' } })"), setup: seedSettings('중3') });
  try {
    await upload(app.page);
    await app.page.getByText('잠시 뒤 다시 해 주세요').waitFor();
    assert.equal(await app.page.evaluate(() => window.__sampleCalls.length), 1);
  } finally { await app.close(); }
});

test('without AI the upload button is replaced by a notice', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seedSettings('중3') });
  try {
    await app.page.goto(app.page.url().split('#')[0] + '#/upload');
    await app.page.getByText('AI 사용을 허용해야 정리할 수 있어요').waitFor();
    assert.equal(await app.page.getByRole('button', { name: 'AI로 정리하기' }).count(), 0);
  } finally { await app.close(); }
});
