// Helpers evaluated inside the page to build mock capability namespaces.
function makeUser(id) { return { id: async () => id, isOwner: () => true, canEdit: () => true }; }
function makeDb(seed) {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('__mockdb') || 'null'); } catch (e) {}
  const docs = new Map(Object.entries(saved || seed || {}));
  const persist = () => localStorage.setItem('__mockdb', JSON.stringify(Object.fromEntries(docs)));
  window.__db = docs;
  const snap = (path) => ({ id: path.split('/').pop(), exists: docs.has(path), data: () => (docs.has(path) ? JSON.parse(JSON.stringify(docs.get(path))) : undefined), metadata: {} });
  function query(colPath, filters) {
    return {
      where(f, op, v) { return query(colPath, filters.concat([[f, op, v]])); },
      orderBy() { return this; }, limit() { return this; },
      async get() {
        const out = [];
        for (const [p] of docs) {
          const rest = p.slice(colPath.length + 1);
          if (p.startsWith(colPath + '/') && !rest.includes('/')) {
            const d = docs.get(p);
            if (filters.every(([f, , v]) => d[f] === v)) out.push(snap(p));
          }
        }
        return { docs: out, size: out.length, empty: !out.length, docChanges: () => [], metadata: {} };
      }
    };
  }
  function doc(path) {
    return {
      id: path.split('/').pop(), path,
      async get() { return snap(path); },
      async set(d) { docs.set(path, JSON.parse(JSON.stringify(d))); persist(); },
      async update(d) { docs.set(path, Object.assign({}, docs.get(path), JSON.parse(JSON.stringify(d)))); persist(); },
      async delete() { docs.delete(path); persist(); },
      collection(p) { return collection(path + '/' + p); }
    };
  }
  function collection(path) { const q = query(path, []); q.doc = (id) => doc(path + '/' + id); q.path = path; return q; }
  return { doc, collection };
}
