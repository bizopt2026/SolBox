import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openApp, HELPERS } from './harness.mjs';
import { seedSettings } from './util.mjs';

const DRILL = "({ problem: '$x^2+x-6=0$의 큰 근', choices: [], answer: 'x = 2', answer_short: '2', steps: [{ board: '$(x+3)(x-2)=0$', say: '인수분해' }] })";
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const seed = (grade, notes) => `${seedSettings(grade)} (function(){ ${HELPERS}; ${notes} })();`;
const oneNote = (due = today()) => `seedNote('n_1', SOLUTION, { due: '${due}' });`;
const mocks = (routes) => `({ user: makeUser('u1'), db: makeDb(), sample: makeSample(${routes}) })`;
const getNote = (page, id) => page.evaluate((id) => window.__db.get('data/users/u1/' + id), id);
const addDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

async function answer(page, text) {
  await page.getByLabel('내 답').fill(text);
  await page.getByRole('button', { name: /채점하기|다 풀었어요/ }).click();
}

test('a right answer climbs one stage and earns a star in kid mode', async () => {
  const app = await openApp({ mocks: mocks(`{ drill: ${DRILL} }`), setup: seed('초3', oneNote()), hash: '#/drill/n_1' });
  const { page } = app;
  try {
    await page.getByText('의 큰 근').waitFor();
    await answer(page, '2');
    await page.getByText('맞았어요!').first().waitFor();
    const n = await getNote(page, 'n_1');
    assert.equal(n.stage, 1);
    assert.equal(n.due, addDays(3));
    const s = await page.evaluate(() => window.__db.get('data/users/u1/settings'));
    assert.equal(s.stars, 1);
    const drills = await page.evaluate(() => [...window.__db.keys()].filter((k) => k.includes('/drills/')));
    assert.equal(drills.length, 1);
    assert.deepEqual(app.errors, []);
  } finally { await app.close(); }
});

test('a wrong answer resets to stage 0', async () => {
  const app = await openApp({ mocks: mocks(`{ drill: ${DRILL} }`), setup: seed('중3', `seedNote('n_1', SOLUTION, { stage: 2, due: '${today()}' });`), hash: '#/drill/n_1' });
  const { page } = app;
  try {
    await page.getByText('의 큰 근').waitFor();
    await answer(page, '5');
    await page.getByText('정답은').waitFor();
    const n = await getNote(page, 'n_1');
    assert.equal(n.stage, 0);
    assert.equal(n.due, addDays(1));
    assert.ok(await page.getByRole('button', { name: '다른 문제 받기' }).isVisible());
  } finally { await app.close(); }
});

test('an unsure answer asks AI to grade once', async () => {
  const d = "({ problem: '식 정리', choices: [], answer: '2x+1', answer_short: '2x+1', steps: [{ board: 'a', say: 'b' }] })";
  const app = await openApp({ mocks: mocks(`{ drill: ${d}, grade: { correct: true, feedback: '같은 식이에요' } }`), setup: seed('중3', oneNote()), hash: '#/drill/n_1' });
  const { page } = app;
  try {
    await page.getByText('식 정리').waitFor();
    await answer(page, '1+2x');
    await page.getByText('맞았어요!').first().waitFor();
    const calls = await page.evaluate(() => window.__sampleCalls.filter((c) => c.task === 'grade'));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].opts.modelTier, 'quick');
  } finally { await app.close(); }
});

test('without AI the original problem is re-solved', async () => {
  const app = await openApp({ mocks: "({ user: makeUser('u1'), db: makeDb() })", setup: seed('중3', oneNote()), hash: '#/drill/n_1' });
  const { page } = app;
  try {
    await page.getByText('원래 문제 다시 풀기').first().waitFor();
    await answer(page, '-1');
    await page.getByText('맞았어요!').first().waitFor();
    assert.equal((await getNote(page, 'n_1')).stage, 1);
  } finally { await app.close(); }
});

test('review lists due notes and marks one as done after solving; note shows history', async () => {
  const app = await openApp({ mocks: mocks(`{ drill: ${DRILL} }`), setup: seed('중3', `seedNote('n_1', SOLUTION, { due: '${today()}', createdAt: '2026-11-19T00:00:00Z' }); seedNote('n_2', SOLUTION, { due: '${today()}' }); seedNote('n_3', SOLUTION, { due: '2099-01-01' });`), hash: '#/review' });
  const { page } = app;
  try {
    await page.locator('.today-item').first().waitFor();
    assert.equal(await page.locator('.today-item').count(), 2);
    await page.locator('.today-item').first().getByRole('link', { name: '풀기' }).click();
    await page.getByText('의 큰 근').waitFor();
    await answer(page, '2');
    await page.getByText('맞았어요!').first().waitFor();
    await page.goto(page.url().split('#')[0] + '#/review');
    await page.locator('.today-item').getByText('맞힘').waitFor();
    assert.equal(await page.locator('.today-item').count(), 2);
    await page.goto(page.url().split('#')[0] + '#/note/n_1');
    await page.locator('.drill-hist li').first().waitFor();
    assert.equal(await page.locator('.drill-hist li').count(), 1);
  } finally { await app.close(); }
});
