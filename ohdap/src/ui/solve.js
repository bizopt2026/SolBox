import { newId } from '../store.js';
import { initialSchedule, todayYmd } from '../review.js';
import { solvePrompt, validateSolution, callJson } from '../ai.js';
import { toast } from './dom.js';

async function photoBlob(note) {
  if (!note.photoId) return null;
  try {
    const r = await fetch('/_blob/' + note.photoId);
    return r.ok ? await r.blob() : null;
  } catch (e) { return null; }
}

async function runSolve(ctx, note, photo) {
  const grade = note.grade || ctx.settings.grade;
  const prompt = solvePrompt({ grade, subject: note.subject, problem: note.problem, choices: note.choices, myAnswer: note.myAnswer, memo: note.memo });
  try {
    const result = await callJson(ctx.caps.sample, prompt, { images: photo ? [photo] : undefined, modelTier: 'complex', validate: validateSolution });
    return Object.assign({}, note, { result, status: 'ready', unit: note.unit || result.unit, tags: result.tags || [], error: '' });
  } catch (e) {
    return Object.assign({}, note, { result: null, status: 'failed', error: (e && e.code) || 'unknown' });
  }
}

export async function solveDraft(ctx) {
  const d = ctx.draft;
  let photoId = null;
  if (ctx.caps.assets && d.photo) {
    try { photoId = (await ctx.caps.assets.upload(d.photo)).id || null; } catch (e) { photoId = null; }
  }
  const grade = ctx.settings.grade === 'auto' ? (d.read.grade_guess || 'auto') : ctx.settings.grade;
  const base = Object.assign({
    type: 'note', id: newId('n'), createdAt: new Date().toISOString(), grade,
    subject: d.read.subject || d.subject || '', unit: d.read.unit || '', tags: [],
    problem: d.read.problem, choices: d.read.choices || [], myAnswer: d.myAnswer || '', memo: d.memo || '',
    photoId, result: null, status: 'failed', starred: false
  }, initialSchedule(todayYmd()));
  const note = await runSolve(ctx, base, d.photo);
  try {
    await ctx.store.saveNote(note);
  } catch (e) { ctx.fail(e); return null; }
  ctx.draft = null;
  ctx.go('/note/' + note.id);
  return note;
}

export async function resolveNote(ctx, note, newProblem) {
  const photo = await photoBlob(note);
  const next = await runSolve(ctx, Object.assign({}, note, newProblem ? { problem: newProblem } : {}), photo);
  try { await ctx.store.saveNote(next); } catch (e) { ctx.fail(e); return note; }
  if (next.status !== 'ready') toast('아직 정리하지 못했어요. 잠시 뒤 다시 해 주세요.');
  return next;
}
