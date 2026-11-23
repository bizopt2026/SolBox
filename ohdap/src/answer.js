const CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩';

function toHalfWidth(s) {
  return s.replace(/[！-～]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/　/g, ' ');
}

export function normalizeAnswer(s) {
  let t = toHalfWidth(String(s || '')).toLowerCase();
  t = t.replace(/[−–—]/g, '-');
  t = t.replace(/[①-⑩]/g, (c) => String(CIRCLED.indexOf(c) + 1));
  // thousands separators: 1,234 → 1234 (a comma followed by exactly three digits, no space)
  t = t.replace(/(\d),(?=\d{3}(?!\d))/g, '$1');
  // mixed numbers: "1 1/2" or "1과 1/2" → "3/2"
  t = t.replace(/(-?)(\d+)(?:\s+|\s*과\s*)(\d+)\/(\d+)/g, (m, sg, a, b, c) => `${sg}${Number(a) * Number(c) + Number(b)}/${c}`);
  t = t.replace(/\s*(또는|그리고|\bor\b)\s*/g, ',').replace(/(\d)\s*(와|과)\s*/g, '$1,');
  t = t.replace(/\s+/g, '');
  t = t.replace(/\.+$/, '');
  // a single labelled answer ("x=-1") is just its value; labelled lists keep labels for ordered grading
  if (!t.includes(',')) t = t.replace(/^[a-z]=/, '');
  return t;
}

function numValue(t) {
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  const f = t.match(/^(-?\d+)\/(\d+)$/);
  if (f && Number(f[2]) !== 0) return Number(f[1]) / Number(f[2]);
  return null;
}

// "x=2" → { label: 'x', value: '2' }
function parts(t) {
  return t.split(',').map((x) => x.replace(/\.+$/, '')).filter(Boolean).map((x) => {
    const m = x.match(/^([a-z])=(.*)$/);
    return m ? { label: m[1], value: m[2] } : { label: '', value: x };
  });
}

const same = (a, b) => {
  if (a === b) return true;
  const x = numValue(a), y = numValue(b);
  return x !== null && y !== null && Math.abs(x - y) < 1e-9;
};

export function quickGrade(user, answerShort) {
  const u = normalizeAnswer(user);
  const a = normalizeAnswer(answerShort);
  if (!u) return 'wrong';
  if (u === a) return 'right';
  const up = parts(u), ap = parts(a);
  const labelled = (ps) => ps.length > 1 && ps.every((p) => p.label) && new Set(ps.map((p) => p.label)).size === ps.length;
  if (labelled(up) && labelled(ap)) {
    if (up.length !== ap.length) return 'unsure';
    const ok = ap.every((p) => { const q = up.find((x) => x.label === p.label); return q && same(q.value, p.value); });
    return ok ? 'right' : 'unsure';
  }
  const uv = up.map((p) => p.value), av = ap.map((p) => p.value);
  // unordered set of values (roots etc.)
  if (uv.length === av.length) {
    const rest = av.slice();
    const matched = uv.every((v) => { const i = rest.findIndex((w) => same(v, w)); if (i < 0) return false; rest.splice(i, 1); return true; });
    if (matched) return 'right';
  }
  // a final "wrong" only for one plain number against one plain number
  const plain = (v) => /^-?\d+(\.\d+)?$/.test(v);
  if (uv.length === 1 && av.length === 1 && plain(uv[0]) && plain(av[0])) return 'wrong';
  return 'unsure';
}
