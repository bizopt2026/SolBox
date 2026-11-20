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
      async get() { if (window.__dbDelay && window.__dbDelay[path]) await new Promise((r) => setTimeout(r, window.__dbDelay[path])); return snap(path); },
      async set(d) { docs.set(path, JSON.parse(JSON.stringify(d))); persist(); },
      async update(d) { docs.set(path, Object.assign({}, docs.get(path), JSON.parse(JSON.stringify(d)))); persist(); },
      async delete() { docs.delete(path); persist(); },
      collection(p) { return collection(path + '/' + p); }
    };
  }
  function collection(path) { const q = query(path, []); q.doc = (id) => doc(path + '/' + id); q.path = path; return q; }
  return { doc, collection };
}

// routes: { read: value | [values...] | {reject:{code}} , solve: ..., drill: ..., grade: ... }
// value may also be a function(input) returning the value.
function makeSample(routes) {
  window.__sampleCalls = [];
  const counts = {};
  const sample = async () => ({ text: '' });
  sample.limits = async () => ({ images: { maxCount: 5, maxInputBytes: 20000000 } });
  sample.json = async (input, opts) => {
    const task = (String(input).match(/^TASK: (\w+)/) || [])[1];
    const call = { task, input: String(input), opts: Object.assign({}, opts, { images: undefined }), images: [] };
    window.__sampleCalls.push(call);
    for (const b of (opts && opts.images) || []) {
      const bmp = await createImageBitmap(b);
      call.images.push({ type: b.type, width: bmp.width, height: bmp.height });
    }
    const r = routes[task];
    const i = counts[task] = (counts[task] || 0) + 1;
    let v = Array.isArray(r) ? r[Math.min(i - 1, r.length - 1)] : r;
    if (typeof v === 'function') v = v(input);
    if (v && v.reject) throw v.reject;
    if (v === undefined) throw { code: 'invalid_json', message: 'no route' };
    return JSON.parse(JSON.stringify(v));
  };
  return sample;
}
const READ_ONE = { readable: true, problems: [{ problem: '이차방정식 $x^2-2x-3=0$의 두 근 중 작은 값을 구하시오.', choices: [], subject: '수학', unit: '이차방정식', grade_guess: '중3' }] };
const SOLUTION = {
  answer: 'x = -1', answer_short: '−1', why_wrong: '큰 근을 답했어요.',
  solutions: [
    { title: '인수분해', steps: [{ board: '$x^2-2x-3=0$', say: '식을 봐요' }, { board: '$(x-3)(x+1)=0$', say: '인수분해' }, { board: '$x=3, -1$', say: '근' }, { board: '작은 값 $-1$', say: '답은 마이너스 1' }] },
    { title: '근의 공식', steps: [{ board: '$a=1$', say: '계수' }, { board: '$x=\\frac{2\\pm4}{2}$', say: '공식' }] },
    { title: '완전제곱식', steps: [{ board: '$(x-1)^2=4$', say: '완전제곱' }] }
  ],
  concepts: [{ name: '근의 공식', summary: '언제나 쓸 수 있어요', formula: 'x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}' }],
  mistakes: ['묻는 것을 다시 확인하지 않기'], unit: '이차방정식', tags: ['인수분해']
};
function makeAssets() {
  window.__assets = [];
  return { upload: async (blob) => { const id = 'a' + window.__assets.length; window.__assets.push({ id, type: blob.type, size: blob.size }); return { id, url: '/_blob/' + id, sizeBytes: blob.size, contentType: blob.type }; } };
}

function makeTts() {
  window.__tts = { spoken: [], lineFns: [] };
  window.__ttsLine = (id) => window.__tts.lineFns.forEach((f) => f({ line: 0, id: String(id) }));
  return {
    speak(lines, opts) { window.__tts.spoken.push({ lines, opts }); return new Promise(() => {}); },
    onLine(fn) { window.__tts.lineFns.push(fn); return () => {}; },
    onState() { return () => {}; },
    pause() {}, resume() {}, stop() {}, setRate() {}
  };
}
const KID_SOLUTION = {
  answer: '236', answer_short: '236', why_wrong: '거꾸로 뺐어요.',
  solutions: [{ title: '세로로 빼기', steps: [
    { board: '$503-267$', say: '세로로 계산해요', visual: { type: 'column', op: '-', a: '503', b: '267', marks: {}, focus: [], result: '' } },
    { board: '빌려 오기', say: '빌려 와요', visual: { type: 'column', op: '-', a: '503', b: '267', marks: { h: '4', t: '9', o: '13' }, focus: ['o'], result: '__6' } }
  ] }],
  concepts: [], mistakes: [], unit: '뺄셈', tags: []
};
function seedNote(id, result, extra) {
  const n = Object.assign({ type: 'note', id, createdAt: '2026-11-20T00:00:00Z', grade: '중3', subject: '수학', unit: '이차방정식', tags: [], problem: '문제 ' + id, choices: [], myAnswer: '', memo: '', photoId: null, result, status: 'ready', stage: 0, due: '2026-11-21', starred: false }, extra || {});
  const db = JSON.parse(localStorage.getItem('__mockdb') || '{}');
  db['data/users/u1/' + id] = n;
  localStorage.setItem('__mockdb', JSON.stringify(db));
}

function makeDownloads() {
  window.__downloads = [];
  return { save: async (f) => { window.__downloads.push({ filename: f.filename, size: String(f.data).length }); } };
}
