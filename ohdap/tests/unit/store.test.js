import { test } from 'node:test';
import assert from 'node:assert/strict';
import { newId, createStore, deviceNoteCount, migrateDeviceToAccount, DEVICE_KEY } from '../../src/store.js';
import { makeFakeDb, makeFakeLocalStorage, fakeUser } from './fake-db.js';

const note = (id, createdAt, extra = {}) => ({ type: 'note', id, createdAt, problem: 'p' + id, stage: 0, due: '2026-11-21', ...extra });

async function bothStores() {
  const db = makeFakeDb();
  const account = await createStore({ db, user: fakeUser('u1'), localStorage: makeFakeLocalStorage() });
  const device = await createStore({ db: null, user: null, localStorage: makeFakeLocalStorage() });
  return { db, account, device };
}

test('newId has prefix and is unique', () => {
  const a = newId('n'), b = newId('n');
  assert.ok(a.startsWith('n_'));
  assert.notEqual(a, b);
});

test('store kind depends on db and user', async () => {
  const { account, device } = await bothStores();
  assert.equal(account.kind, 'account');
  assert.equal(device.kind, 'device');
});

for (const kind of ['account', 'device']) {
  test(`${kind}: notes save, list newest first, get, delete`, async () => {
    const s = (await bothStores())[kind];
    await s.saveNote(note('n_a', '2026-11-19T00:00:00Z'));
    await s.saveNote(note('n_b', '2026-11-20T00:00:00Z'));
    assert.deepEqual((await s.listNotes()).map((n) => n.id), ['n_b', 'n_a']);
    assert.equal((await s.getNote('n_a')).problem, 'pn_a');
    await s.deleteNote('n_a');
    assert.equal(await s.getNote('n_a'), null);
  });

  test(`${kind}: settings default and save`, async () => {
    const s = (await bothStores())[kind];
    assert.deepEqual(await s.getSettings(), { type: 'settings', grade: 'auto', onboarded: false, stars: 0 });
    await s.saveSettings({ type: 'settings', grade: '초3', onboarded: true, stars: 2 });
    assert.equal((await s.getSettings()).grade, '초3');
  });

  test(`${kind}: drills save and list by note`, async () => {
    const s = (await bothStores())[kind];
    await s.saveNote(note('n_a', '2026-11-19T00:00:00Z'));
    await s.saveDrill({ id: 'd_1', noteId: 'n_a', createdAt: '2026-11-20T00:00:00Z', correct: true });
    await s.saveDrill({ id: 'd_2', noteId: 'n_a', createdAt: '2026-11-21T00:00:00Z', correct: false });
    assert.deepEqual((await s.listDrills('n_a')).map((d) => d.id), ['d_2', 'd_1']);
    assert.deepEqual(await s.listDrills('n_x'), []);
  });
}

test('account store writes to the private per-user paths', async () => {
  const { db, account } = await bothStores();
  await account.saveNote(note('n_a', '2026-11-19T00:00:00Z'));
  await account.saveSettings({ type: 'settings', grade: '중1', onboarded: true, stars: 0 });
  await account.saveDrill({ id: 'd_1', noteId: 'n_a', createdAt: 'x', correct: true });
  assert.deepEqual(db.writes, ['data/users/u1/n_a', 'data/users/u1/settings', 'data/users/u1/n_a/drills/d_1']);
});

test('account listNotes ignores the settings doc', async () => {
  const { account } = await bothStores();
  await account.saveSettings({ type: 'settings', grade: '중1', onboarded: true, stars: 0 });
  assert.deepEqual(await account.listNotes(), []);
});

test('saveNote surfaces db write errors', async () => {
  const { db, account } = await bothStores();
  db.failWrites = true;
  await assert.rejects(account.saveNote(note('n_a', 'x')), (e) => e.code === 'quota_exceeded');
});

test('saved note is a copy, not the caller object', async () => {
  const { device } = await bothStores();
  const n = note('n_a', 'x');
  await device.saveNote(n);
  n.problem = 'changed';
  assert.equal((await device.getNote('n_a')).problem, 'pn_a');
});

test('migrate device notes into the account without duplicates', async () => {
  const ls = makeFakeLocalStorage();
  const device = await createStore({ db: null, user: null, localStorage: ls });
  await device.saveNote(note('n_a', '2026-11-19T00:00:00Z'));
  await device.saveNote(note('n_b', '2026-11-20T00:00:00Z'));
  await device.saveDrill({ id: 'd_1', noteId: 'n_b', createdAt: 'x', correct: true });
  await device.saveSettings({ type: 'settings', grade: '초3', onboarded: true, stars: 2 });
  assert.equal(deviceNoteCount(ls), 2);

  const db = makeFakeDb();
  const account = await createStore({ db, user: fakeUser('u1'), localStorage: ls });
  await account.saveNote(note('n_a', '2026-11-19T00:00:00Z', { problem: 'account copy' }));
  await account.saveSettings({ type: 'settings', grade: '초4', onboarded: true, stars: 3 });

  assert.deepEqual(await migrateDeviceToAccount(ls, account), { moved: 1, skipped: 1 });
  assert.equal(ls.getItem(DEVICE_KEY), null);
  assert.equal(deviceNoteCount(ls), 0);
  assert.equal((await account.getNote('n_a')).problem, 'account copy');
  assert.equal((await account.listDrills('n_b')).length, 1);
  const s = await account.getSettings();
  assert.equal(s.grade, '초4');
  assert.equal(s.stars, 5);
});

// Final review fix 7
test('migration copies missing drills of a note that is already in the account', async () => {
  const ls = makeFakeLocalStorage();
  const device = await createStore({ db: null, user: null, localStorage: ls });
  await device.saveNote(note('n_a', '2026-11-19T00:00:00Z'));
  await device.saveDrill({ id: 'd_1', noteId: 'n_a', createdAt: 'x', correct: true });
  await device.saveDrill({ id: 'd_2', noteId: 'n_a', createdAt: 'y', correct: false });
  const account = await createStore({ db: makeFakeDb(), user: fakeUser('u1'), localStorage: ls });
  await account.saveNote(note('n_a', '2026-11-19T00:00:00Z'));
  await account.saveDrill({ id: 'd_1', noteId: 'n_a', createdAt: 'x', correct: true });
  await migrateDeviceToAccount(ls, account);
  assert.deepEqual((await account.listDrills('n_a')).map((d) => d.id).sort(), ['d_1', 'd_2']);
});
