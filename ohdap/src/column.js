const PLACES = ['o', 't', 'h', 'th'];
const OPS = ['+', '-', '×'];

export function isValidColumn(v) {
  if (!v || v.type !== 'column' || !OPS.includes(v.op)) return false;
  if (!/^\d{1,4}$/.test(String(v.a)) || !/^\d{1,4}$/.test(String(v.b))) return false;
  if (v.result != null && !/^[\d_]{0,4}$/.test(String(v.result))) return false;
  if (v.marks != null && typeof v.marks !== 'object') return false;
  if (v.focus != null && !Array.isArray(v.focus)) return false;
  return true;
}

function padLeft(arr, width) {
  return Array(width - arr.length).fill('').concat(arr);
}

export function columnModel(v) {
  const a = String(v.a).split('');
  const b = String(v.b).split('');
  const res = String(v.result || '').split('').map((c) => (c === '_' ? '' : c));
  const width = Math.max(a.length, b.length, res.length);
  const placeAt = (i) => PLACES[width - 1 - i];
  const marks = v.marks || {};
  const focus = v.focus || [];
  return {
    width,
    op: v.op,
    rows: {
      marks: Array.from({ length: width }, (_, i) => String(marks[placeAt(i)] || '')),
      a: padLeft(a, width),
      b: padLeft(b, width),
      result: padLeft(res, width),
      focus: Array.from({ length: width }, (_, i) => focus.includes(placeAt(i)))
    }
  };
}
