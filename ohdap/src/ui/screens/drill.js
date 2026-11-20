import { h, icon } from '../dom.js';
import { topbar } from '../chrome.js';
import { renderMathText } from '../../mathtext.js';
import { isElementary } from '../../grades.js';
import { applyResult, todayYmd, MASTERED, INTERVALS, isDue } from '../../review.js';
import { quickGrade } from '../../answer.js';
import { drillPrompt, gradePrompt, validateDrill, validateGrade, callJson } from '../../ai.js';
import { newId } from '../../store.js';
import { renderColumn } from '../columnview.js';

function mathNode(tag, cls, text) {
  const el = h(tag, { class: cls });
  el.innerHTML = renderMathText(text, window.katex);
  return el;
}

export async function renderDrill(ctx, params) {
  const note = await ctx.store.getNote(params.id);
  if (!note || !note.result) { setTimeout(() => ctx.go('/home'), 0); return h('div', { class: 'screen' }); }
  const w = ctx.words;
  const kid = isElementary(ctx.settings.grade);
  const body = h('main', { class: 'page' });
  const el = h('div', { class: 'screen' }, topbar(kid ? '비슷한 문제 도전' : '비슷한 문제', '/note/' + note.id), body);

  let drill = null;
  let notice = '';
  if (params.drillId) {
    const old = (await ctx.store.listDrills(note.id)).find((d) => d.id === params.drillId);
    if (old) drill = { replay: true, kind: old.kind, problem: old.problem, choices: old.choices || [], answer: old.answer, answer_short: old.answer_short, steps: old.steps || [] };
  }
  const original = () => ({ kind: 'original', problem: note.problem, choices: note.choices || [], answer: note.result.answer, answer_short: note.result.answer_short, steps: note.result.solutions[0].steps });

  async function load() {
    body.textContent = '';
    body.appendChild(h('p', { class: 'muted', role: 'status' }, '문제를 만드는 중…'));
    if (!drill) {
      try {
        const d = await callJson(ctx.caps.sample, drillPrompt({ grade: note.grade || ctx.settings.grade, note }), { modelTier: 'default', validate: validateDrill, fresh: true });
        drill = Object.assign({ kind: 'similar' }, d);
      } catch (e) {
        drill = original();
        notice = 'AI를 쓸 수 없어서 원래 문제 다시 풀기로 복습해요.';
      }
    }
    paintQuestion();
  }

  function paintQuestion() {
    body.textContent = '';
    const concept = note.result.concepts[0];
    if (notice) body.appendChild(h('div', { class: 'notice' }, notice));
    body.appendChild(h('span', { class: 'tag soft' }, drill.kind === 'original' ? '원래 문제 다시 풀기' : concept ? `같은 개념 · ${concept.name}` : '같은 개념'));
    const firstVisual = kid && drill.steps[0] && drill.steps[0].visual;
    body.appendChild(h('section', { class: 'card' }, mathNode('div', 'mtext big-q', drill.problem), firstVisual ? renderColumn(firstVisual, {}) : null));
    const input = h('input', { class: 'input answer-input', id: 'ans', placeholder: kid ? '답을 적어요' : '답을 적어 주세요', autocomplete: 'off' });
    if (drill.choices.length) {
      body.appendChild(h('div', { class: 'choice-btns' }, drill.choices.map((c, i) => {
        const b = h('button', { type: 'button', class: 'btn choice' }, `${i + 1}. `, mathNode('span', '', c));
        b.addEventListener('click', () => { input.value = String(i + 1); });
        return b;
      })));
    }
    body.appendChild(h('label', { class: 'field' }, h('span', {}, '내 답'), input));
    const result = h('div', { class: 'drill-result' });
    const submit = h('button', { class: 'btn primary big', onclick: () => grade(input.value) }, kid ? '다 풀었어요' : '채점하기');
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') grade(input.value); });
    body.append(submit, result);

    let done = false;
    async function grade(ans) {
      if (done || !String(ans).trim()) return;
      submit.disabled = true;
      let verdict = quickGrade(ans, drill.answer_short);
      if (verdict === 'unsure' && drill.kind === 'similar' && ctx.caps.sample) {
        try {
          const g = await callJson(ctx.caps.sample, gradePrompt({ problem: drill.problem, answer: drill.answer, userAnswer: ans }), { modelTier: 'quick', validate: validateGrade });
          verdict = g.correct ? 'right' : 'wrong';
        } catch (e) { verdict = 'unsure'; }
      }
      if (verdict === 'unsure') {
        result.textContent = '';
        const ansEl = h('div', { class: 'notice' }, '정답: ', mathNode('b', '', drill.answer), h('p', { class: 'muted' }, '내 답과 같은 뜻인지 직접 골라 주세요.'));
        result.append(ansEl, h('div', { class: 'grid2' },
          h('button', { class: 'btn primary', onclick: () => finish(ans, true) }, '맞았어요'),
          h('button', { class: 'btn', onclick: () => finish(ans, false) }, '틀렸어요')));
        return;
      }
      finish(ans, verdict === 'right');
    }

    async function finish(ans, correct) {
      if (done) return;
      done = true;
      submit.disabled = true;
      input.disabled = true;
      const today = todayYmd();
      // Only a scheduled review (due today or overdue, not a replayed old drill) moves the ladder or earns a star.
      const counts = !drill.replay && isDue(note, today);
      const sched = counts ? applyResult(note, correct, today) : { stage: note.stage, due: note.due };
      const rec = { id: newId('d'), noteId: note.id, createdAt: new Date().toISOString(), kind: drill.kind, practice: !counts, problem: drill.problem, choices: drill.choices, answer: drill.answer, answer_short: drill.answer_short, steps: drill.steps, userAnswer: String(ans), correct };
      try {
        if (counts) {
          const updated = Object.assign({}, note, sched, { lastReviewed: today, lastCorrect: correct });
          await ctx.store.saveNote(updated);
          Object.assign(note, updated);
        }
        await ctx.store.saveDrill(rec);
        if (counts && kid && correct) await ctx.saveSettings(Object.assign({}, ctx.settings, { stars: (ctx.settings.stars || 0) + 1 }));
      } catch (e) { ctx.fail(e); }
      result.textContent = '';
      ctx.boardDrill = { title: '풀이', steps: drill.steps, problem: drill.problem, back: '/drill/' + note.id + '/' + rec.id };
      const boardBtn = drill.steps.length ? h('a', { class: 'btn dark', href: `#/board/${note.id}/drill` }, w.board) : null;
      if (correct) {
        const nextText = !counts ? `연습으로 기록했어요. ${w.review} 날짜는 그대로예요.`
          : sched.stage >= MASTERED ? '이 문제를 정복했어요!' : `${w.review} ${sched.stage + 1}단계로 올라갔어요. ${INTERVALS[sched.stage]}일 뒤에 다시 만나요.`;
        result.append(h('section', { class: 'result ok', role: 'status' }, h('span', { class: 'result-title' }, icon('check', 22), kid && counts ? w.rightMsg : '맞았어요!'), h('p', {}, nextText)),
          h('div', { class: 'grid2' }, boardBtn || h('span'), h('a', { class: 'btn primary', href: '#/review' }, kid ? '다음 문제' : '다음 복습')));
      } else {
        result.append(h('section', { class: 'result bad', role: 'status' }, h('span', { class: 'result-title' }, icon('cross', 22), kid ? w.wrongMsg : '아쉬워요'),
          h('p', {}, '정답은 ', mathNode('b', '', drill.answer_short), counts ? '예요. 내일 한 번 더 풀어 봐요.' : `예요. 연습으로 기록했어요. ${w.review} 날짜는 그대로예요.`)),
          h('div', { class: 'grid2' }, boardBtn || h('span'), h('button', { class: 'btn', onclick: () => { drill = null; notice = ''; load(); } }, '다른 문제 받기')));
      }
    }
  }

  load();
  return el;
}
