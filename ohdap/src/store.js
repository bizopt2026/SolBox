export const DEVICE_KEY = 'ohdap:v1';
const DEFAULT_SETTINGS = { type: 'settings', grade: 'auto', onboarded: false, stars: 0 };

export function newId(prefix) {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

const clone = (o) => JSON.parse(JSON.stringify(o));
const byCreatedDesc = (a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0);

function readDevice(ls) {
  try {
    const raw = ls && ls.getItem(DEVICE_KEY);
    const d = raw ? JSON.parse(raw) : null;
    if (d && typeof d === 'object') return { notes: d.notes || {}, drills: d.drills || {}, settings: d.settings || null };
  } catch (e) { /* unreadable → empty */ }
  return { notes: {}, drills: {}, settings: null };
}

function writeDevice(ls, data) {
  ls.setItem(DEVICE_KEY, JSON.stringify(data));
}

function deviceStore(ls) {
  return {
    kind: 'device',
    async listNotes() { return Object.values(readDevice(ls).notes).map(clone).sort(byCreatedDesc); },
    async getNote(id) { const n = readDevice(ls).notes[id]; return n ? clone(n) : null; },
    async saveNote(note) { const d = readDevice(ls); d.notes[note.id] = clone(note); writeDevice(ls, d); },
    async deleteNote(id) { const d = readDevice(ls); delete d.notes[id]; delete d.drills[id]; writeDevice(ls, d); },
    async listDrills(noteId) { return Object.values(readDevice(ls).drills[noteId] || {}).map(clone).sort(byCreatedDesc); },
    async saveDrill(drill) { const d = readDevice(ls); d.drills[drill.noteId] = Object.assign({}, d.drills[drill.noteId], { [drill.id]: clone(drill) }); writeDevice(ls, d); },
    async getSettings() { return Object.assign({}, DEFAULT_SETTINGS, readDevice(ls).settings || {}); },
    async saveSettings(s) { const d = readDevice(ls); d.settings = clone(s); writeDevice(ls, d); }
  };
}

function accountStore(db, uid) {
  const base = `data/users/${uid}`;
  const col = () => db.collection(base);
  const docData = async (ref) => { const s = await ref.get(); return s.exists ? s.data() : null; };
  return {
    kind: 'account',
    async listNotes() {
      const snap = await col().where('type', '==', 'note').get();
      return snap.docs.map((d) => d.data()).sort(byCreatedDesc);
    },
    async getNote(id) { return docData(db.doc(`${base}/${id}`)); },
    async saveNote(note) { await db.doc(`${base}/${note.id}`).set(clone(note)); },
    async deleteNote(id) { await db.doc(`${base}/${id}`).delete(); },
    async listDrills(noteId) {
      const snap = await db.collection(`${base}/${noteId}/drills`).get();
      return snap.docs.map((d) => d.data()).sort(byCreatedDesc);
    },
    async saveDrill(drill) { await db.doc(`${base}/${drill.noteId}/drills/${drill.id}`).set(clone(drill)); },
    async getSettings() { return Object.assign({}, DEFAULT_SETTINGS, (await docData(db.doc(`${base}/settings`))) || {}); },
    async saveSettings(s) { await db.doc(`${base}/settings`).set(clone(s)); }
  };
}

export async function createStore({ db, user, localStorage }) {
  if (db && user) {
    const uid = await user.id();
    if (uid) return accountStore(db, uid);
  }
  return deviceStore(localStorage);
}

export function deviceNoteCount(ls) {
  return Object.keys(readDevice(ls).notes).length;
}

export async function migrateDeviceToAccount(ls, store) {
  const d = readDevice(ls);
  let moved = 0, skipped = 0;
  for (const note of Object.values(d.notes)) {
    if (await store.getNote(note.id)) { skipped++; continue; }
    await store.saveNote(note);
    for (const drill of Object.values(d.drills[note.id] || {})) await store.saveDrill(drill);
    moved++;
  }
  if (d.settings) {
    const s = await store.getSettings();
    const merged = s.onboarded ? Object.assign({}, s, { stars: (s.stars || 0) + (d.settings.stars || 0) }) : Object.assign({}, d.settings);
    await store.saveSettings(merged);
  }
  ls.removeItem(DEVICE_KEY);
  return { moved, skipped };
}
