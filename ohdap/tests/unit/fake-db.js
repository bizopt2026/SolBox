// In-memory stand-in for the artifact `db` namespace (only what store.js uses).
export function makeFakeDb() {
  const docs = new Map();
  const writes = [];
  const snap = (path) => ({ id: path.split('/').pop(), exists: docs.has(path), data: () => (docs.has(path) ? structuredClone(docs.get(path)) : undefined), metadata: {} });
  function query(colPath, filters) {
    return {
      where(f, op, v) { return query(colPath, filters.concat([[f, op, v]])); },
      orderBy() { return this; },
      limit() { return this; },
      async get() {
        const out = [];
        for (const [p, d] of docs) {
          if (p.startsWith(colPath + '/') && !p.slice(colPath.length + 1).includes('/') && filters.every(([f, , v]) => d[f] === v)) out.push(snap(p));
        }
        return { docs: out, size: out.length, empty: !out.length };
      }
    };
  }
  function doc(path) {
    return {
      id: path.split('/').pop(), path,
      async get() { return snap(path); },
      async set(d) { if (db.failWrites) throw Object.assign(new Error('quota'), { code: 'quota_exceeded' }); writes.push(path); docs.set(path, structuredClone(d)); },
      async update(d) { docs.set(path, Object.assign({}, docs.get(path), structuredClone(d))); },
      async delete() { docs.delete(path); },
      collection(p) { return collection(path + '/' + p); }
    };
  }
  function collection(path) { const q = query(path, []); q.doc = (id) => doc(path + '/' + id); return q; }
  const db = { doc, collection, docs, writes, failWrites: false };
  return db;
}

export function makeFakeLocalStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), _m: m };
}

export const fakeUser = (id) => ({ id: async () => id });
