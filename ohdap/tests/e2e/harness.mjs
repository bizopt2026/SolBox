import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const APP = pathToFileURL(fileURLToPath(new URL('../../index.html', import.meta.url))).href;
const MOCK = readFileSync(fileURLToPath(new URL('./mock-claude.js', import.meta.url)), 'utf8');

/**
 * Open the built app.
 * mocks: null → no window.claude; otherwise a string of JS that evaluates to the mock config object
 *        (built in the page so functions survive), e.g. "({ user: makeUser('u1') })".
 * setup: extra init script source run before the mock (e.g. seed localStorage).
 */
export async function openApp({ mocks = null, setup = '', viewport = { width: 390, height: 844 }, hash = '' } = {}) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.addInitScript({ content: setup });
  await page.addInitScript({ content: `window.__mockConfig = ${mocks === null ? 'null' : `(function(){ ${HELPERS}; return ${mocks}; })()`};` });
  await page.addInitScript({ content: MOCK });
  await page.goto(APP + hash);
  return { page, context, browser, errors, close: () => browser.close() };
}

export const HELPERS = readFileSync(fileURLToPath(new URL('./mock-helpers.js', import.meta.url)), 'utf8');
