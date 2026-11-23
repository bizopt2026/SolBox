import { h, icon } from '../dom.js';
import { topbar } from '../chrome.js';
import { renderMathText } from '../../mathtext.js';
import { isElementary } from '../../grades.js';
import { MASTERED, todayYmd, isDue } from '../../review.js';
import { resolveNote } from '../solve.js';
import { aiErrorText } from '../aimsg.js';
import { renderColumn } from '../columnview.js';

export function mathEl(tag, cls, text) {
  const el = h(tag, { class: cls });
  el.innerHTML = renderMathText(text, window.katex);
  return el;
}

function firstVisual(note) {
  for (const s of (note.result && note.result.solutions) || []) for (const st of s.steps) if (st.visual) return st.visual;
  return null;
}

export async function renderNote(ctx, params) {
  const note = await ctx.store.getNote(params.id);
  if (!note) return h('div', { class: 'screen' }, topbar('노트', '/home'), h('main', { class: 'page' }, h('p', { class: 'muted' }, '노트를 찾지 못했어요.')));
  const w = ctx.words;
  const kid = isElementary(ctx.settings.grade);
  const r = note.result;

  const star = h('button', { class: 'iconbtn star' + (note.starred ? ' on' : ''), 'aria-label': '다시보기 표시', 'aria-pressed': note.starred ? 'true' : 'false', onclick: async () => {
    note.starred = !note.starred;
    star.classList.toggle('on', note.starred); star.setAttribute('aria-pressed', note.starred ? 'true' : 'false');
    try { await ctx.store.saveNote(note); } catch (e) { ctx.fail(e); }
  } }, icon('star'));

  const photoSlot = h('div');
  const photoBtn = note.photoId ? h('button', { class: 'linkish', type: 'button', onclick: () => {
    photoSlot.textContent = '';
    photoSlot.appendChild(h('img', { class: 'photo-full', src: '/_blob/' + note.photoId, alt: '원본 문제 사진' }));
    photoBtn.remove();
  } }, '원본 사진 보기') : null;

  const visual = kid ? firstVisual(note) : null;
  const problemCard = h('section', { class: 'card' },
    mathEl('div', 'mtext', note.problem),
    visual ? renderColumn(visual, {}) : null,
    note.choices && note.choices.length ? h('ol', { class: 'choices' }, note.choices.map((c) => mathEl('li', 'mtext', c))) : null,
    h('div', { class: 'row-between muted' }, photoBtn || h('span'), h('span', {}, new Date(note.createdAt).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' }))),
    photoSlot);

  const body = [problemCard];

  if (note.status !== 'ready' || !r) {
    const retry = h('button', { class: 'btn primary big', onclick: async () => {
      retry.disabled = true; retry.textContent = '정리하는 중…';
      await resolveNote(ctx, note);
      ctx.refresh();
    } }, '다시 정리');
    body.push(h('div', { class: 'notice warn' }, note.error ? aiErrorText({ code: note.error }) : '아직 정리하지 못했어요.'), retry);
  } else {
    body.push(h('section', { class: note.myAnswer ? 'grid2' : '' },
      note.myAnswer ? h('div', { class: 'answer-card bad' }, h('span', { class: 'answer-label' }, icon('cross', 14), w.myAnswer), mathEl('span', 'answer-val', note.myAnswer)) : null,
      h('div', { class: 'answer-card ok' }, h('span', { class: 'answer-label' }, icon('check', 14), w.answer), mathEl('span', 'answer-val', r.answer_short), r.answer && r.answer !== r.answer_short ? mathEl('span', 'answer-long', r.answer) : null)));
    if (r.why_wrong) body.push(h('section', { class: 'why' }, h('h2', { class: 'h3' }, w.why), mathEl('p', 'why-text', r.why_wrong)));
    body.push(h('a', { class: 'btn dark big board-link', href: `#/board/${note.id}/0` }, h('span', { class: 'play-dot' }, icon('play', 18)), w.board));

    let sol = 0;
    const stepsEl = h('ol', { class: 'steps' });
    const tabs = r.solutions.map((s, i) => h('button', { role: 'tab', class: 'tab-btn', 'aria-selected': 'false', onclick: () => { sol = i; paintSol(); } }, s.title));
    function paintSol() {
      tabs.forEach((t, i) => { t.classList.toggle('on', i === sol); t.setAttribute('aria-selected', i === sol ? 'true' : 'false'); });
      stepsEl.textContent = '';
      r.solutions[sol].steps.forEach((st, i) => stepsEl.appendChild(h('li', { class: 'step' },
        h('span', { class: 'step-n' }, String(i + 1)),
        h('div', { class: 'step-body' }, st.visual && kid ? renderColumn(st.visual, { marks: true, result: true }) : mathEl('div', 'step-board', st.board), h('p', { class: 'step-say' }, st.say)))));
    }
    paintSol();
    body.push(h('section', { class: 'sec' },
      h('h2', { class: 'h2' }, r.solutions.length > 1 ? `${w.solutions} ${r.solutions.length}가지` : w.solutions),
      r.solutions.length > 1 ? h('div', { role: 'tablist', class: 'tabs' }, tabs) : null,
      stepsEl));

    if (r.concepts.length) body.push(h('section', { class: 'sec' }, h('h2', { class: 'h2' }, w.concepts),
      r.concepts.map((c) => h('div', { class: 'card concept' + (kid ? ' kid-concept' : '') },
        h('span', { class: 'concept-name' }, c.name),
        c.formula ? mathEl('div', 'formula', '$' + c.formula + '$') : null,
        mathEl('p', 'concept-sum', c.summary)))));
    if (r.mistakes.length) body.push(h('section', { class: 'sec' }, h('h2', { class: 'h2' }, w.mistakes),
      h('ul', { class: 'card mistakes' }, r.mistakes.map((m) => mathEl('li', '', m)))));

    const today = todayYmd();
    const stages = ['1일', '3일', '7일', '14일', '정복'];
    const due = isDue(note, today);
    body.push(h('section', { class: 'card' },
      h('div', { class: 'row-between' }, h('h2', { class: 'h3' }, `${w.review} 단계`),
        h('span', { class: 'due-text' }, note.stage >= MASTERED ? '정복했어요' : due ? `오늘 ${w.review}할 차례` : `${note.due} ${w.review}`)),
      h('ol', { class: 'ladder', 'aria-label': `${w.review} 5단계 중 ${Math.min(note.stage + 1, 5)}단계` },
        stages.map((s, i) => h('li', { class: i < note.stage ? 'done' : i === note.stage ? 'on' : '' }, h('span', { class: 'bar' }), s))),
      h('a', { class: 'btn primary big', href: `#/drill/${note.id}` }, w.drill),
      h('span', { class: 'muted center small' }, 'AI 풀이는 틀릴 수 있어요. 이상하면 아래에서 문제를 고쳐 다시 풀게 해 주세요.')));
    const hist = h('section', { class: 'sec drill-hist-sec' });
    body.push(hist);
    ctx.store.listDrills(note.id).then((ds) => {
      if (!ds.length) return;
      hist.append(h('h2', { class: 'h3' }, '지난 연습'), h('ul', { class: 'drill-hist' }, ds.slice(0, 10).map((d) => h('li', {},
        h('a', { href: `#/drill/${note.id}/${d.id}` },
          h('span', { class: d.correct ? 'got ok' : 'got bad' }, icon(d.correct ? 'check' : 'cross', 14), d.correct ? '맞음' : '틀림'),
          h('span', { class: 'hist-text' }, String(d.problem || '').replace(/\$/g, '').slice(0, 40)),
          h('span', { class: 'muted small' }, new Date(d.createdAt).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' })))))));
    }).catch(() => {});
  }

  const fix = h('textarea', { class: 'input', rows: '3', 'aria-label': '문제 고치기' });
  fix.value = note.problem;
  const fixBtn = h('button', { class: 'btn', onclick: async () => {
    fixBtn.disabled = true; fixBtn.textContent = '다시 푸는 중…';
    await resolveNote(ctx, note, fix.value);
    ctx.refresh();
  } }, '고친 문제로 다시 풀기');
  body.push(h('details', { class: 'fix' }, h('summary', {}, '문제 고쳐 다시 풀기'), fix, fixBtn));
  const delBox = h('div', { class: 'confirm-del', hidden: true, role: 'group', 'aria-label': '노트 지우기 확인' },
    h('span', {}, '이 노트와 연습 기록을 지울까요? 되돌릴 수 없어요.'),
    h('div', { class: 'grid2' },
      h('button', { class: 'btn', type: 'button', onclick: () => { delBox.hidden = true; } }, '취소'),
      h('button', { class: 'btn danger', type: 'button', onclick: async () => {
        try { await ctx.store.deleteNote(note.id); ctx.go('/home'); } catch (e) { ctx.fail(e); }
      } }, '지우기')));
  body.push(h('button', { class: 'btn danger-link', type: 'button', onclick: () => { delBox.hidden = false; } }, '노트 지우기'), delBox);

  return h('div', { class: 'screen' },
    topbar([note.subject, note.unit].filter(Boolean).join(' · ') || '노트', '/home', star),
    h('main', { class: 'page' }, body));
}
