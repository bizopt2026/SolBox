export const INTERVALS = [1, 3, 7, 14];
export const MASTERED = 4;

const pad2 = (n) => String(n).padStart(2, '0');

export function addDays(ymd, n) {
  const [y, m, d] = ymd.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d, 12));
  t.setUTCDate(t.getUTCDate() + n);
  return `${t.getUTCFullYear()}-${pad2(t.getUTCMonth() + 1)}-${pad2(t.getUTCDate())}`;
}

export function todayYmd(now = new Date()) {
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
}

function daysBetween(a, b) {
  const p = (s) => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d); };
  return Math.round((p(b) - p(a)) / 86400000);
}

export function initialSchedule(today) {
  return { stage: 0, due: addDays(today, INTERVALS[0]) };
}

export function applyResult(note, correct, today) {
  if (!correct) return { stage: 0, due: addDays(today, INTERVALS[0]) };
  const next = Math.min((note.stage || 0) + 1, MASTERED);
  if (next >= MASTERED) return { stage: MASTERED, due: null };
  return { stage: next, due: addDays(today, INTERVALS[next]) };
}

export function isDue(note, today) {
  return note.stage < MASTERED && !!note.due && note.due <= today;
}

export function dueNotes(notes, today) {
  const key = (n) => `${n.due}|${n.createdAt || ''}`;
  return notes.filter((n) => isDue(n, today)).sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
}

export function stageLabel(note, today) {
  if (note.stage >= MASTERED) return '정복';
  const step = `${note.stage + 1}단계`;
  if (!note.due || note.due <= today) return `오늘 복습 · ${step}`;
  return `복습 ${step} · ${daysBetween(today, note.due)}일 뒤`;
}
