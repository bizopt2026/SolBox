import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp } from './harness.mjs';

test('app boots without window.claude and shows its name', async () => {
  const app = await openApp({ mocks: null });
  try {
    await app.page.waitForSelector('#app');
    assert.ok((await app.page.title()) === '오답노트');
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});
