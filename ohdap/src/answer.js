const CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩';

function toHalfWidth(s) {
  return s.replace(/[！-～]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0)).replace(/　/g, ' ');
}

export function normalizeAnswer(s) {
  let t = toHalfWidth(String(s || '')).toLowerCase();
  t = t.replace(/[−–—]/g, '-');
  t = t.replace(/[①-⑩]/g, (c) => String(CIRCLED.indexOf(c) + 1));
  t = t.replace(/\s*(또는|그리고|\bor\b)\s*/g, ',').replace(/(\d)\s*(와|과)\s*/g, '$1,');
  t = t.replace(/\s+/g, '');
  t = t.replace(/(^|,)[a-z]=/g, '$1');
  t = t.replace(/\.+$/, '');
  return t;
}

function numValue(t) {
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t);
  const f = t.match(/^(-?\d+)\/(\d+)$/);
  if (f && Number(f[2]) !== 0) return Number(f[1]) / Number(f[2]);
  return null;
}

function parts(t) {
  return t.split(',').map((x) => x.replace(/\.+$/, '')).filter(Boolean);
}

export function quickGrade(user, answerShort) {
  const u = normalizeAnswer(user);
  const a = normalizeAnswer(answerShort);
  if (!u) return 'wrong';
  if (u === a) return 'right';
  const up = parts(u), ap = parts(a);
  const un = up.map(numValue), an = ap.map(numValue);
  if (un.every((v) => v !== null) && an.every((v) => v !== null) && an.length) {
    if (un.length !== an.length) return 'wrong';
    const s1 = un.slice().sort((x, y) => x - y), s2 = an.slice().sort((x, y) => x - y);
    return s1.every((v, i) => Math.abs(v - s2[i]) < 1e-9) ? 'right' : 'wrong';
  }
  if (up.length === ap.length && up.slice().sort().join() === ap.slice().sort().join()) return 'right';
  return 'unsure';
}
