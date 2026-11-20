import { h, icon } from '../dom.js';
import { topbar } from '../chrome.js';
import { renderMathText } from '../../mathtext.js';
import { progressSteps } from '../aimsg.js';

export function renderConfirm(ctx) {
  const d = ctx.draft;
  if (!d || !d.read) { setTimeout(() => ctx.go('/upload'), 0); return h('div', { class: 'screen' }); }
  let editing = false;
  const view = h('div', { class: 'problem-view mtext' });
  const area = h('textarea', { class: 'input', rows: '4', 'aria-label': '문제 고치기' });
  area.value = d.read.problem;
  const editHelp = h('span', { class: 'muted' }, '틀린 글자를 고쳐 주세요. 제곱은 x^2 처럼 써도 돼요.');
  const box = h('div', { class: 'edit-box' });
  const toggle = h('button', { type: 'button', class: 'btn small', onclick: () => { if (editing) d.read.problem = area.value; editing = !editing; paint(); } });
  const choices = h('ol', { class: 'choices' });
  const cta = h('button', { class: 'btn primary big', onclick: solve });
  const status = h('div', { role: 'status' });

  function paint() {
    box.textContent = '';
    if (editing) box.append(editHelp, area);
    else { view.innerHTML = renderMathText(d.read.problem, window.katex); box.append(view); }
    toggle.textContent = editing ? '완료' : '고치기';
    toggle.prepend(icon('edit', 14));
    cta.textContent = editing ? '고친 문제로 풀기' : '맞아요, 풀어 주세요';
    choices.textContent = '';
    (d.read.choices || []).forEach((c) => { const li = h('li', { class: 'mtext' }); li.innerHTML = renderMathText(c, window.katex); choices.appendChild(li); });
  }

  async function solve() {
    if (editing) { d.read.problem = area.value; editing = false; paint(); }
    cta.disabled = true; toggle.disabled = true;
    status.textContent = '';
    status.appendChild(progressSteps(2));
    if (ctx.solveDraft) await ctx.solveDraft(ctx);
    cta.disabled = false; toggle.disabled = false;
  }

  const tags = [d.read.subject || d.subject, d.read.unit, d.read.grade_guess].filter(Boolean);
  paint();
  return h('div', { class: 'screen' },
    topbar('문제를 맞게 읽었나요?', '/upload'),
    h('main', { class: 'page' },
      progressSteps(2),
      h('div', { class: 'confirm-head' },
        d.photoUrl ? h('img', { class: 'thumb', src: d.photoUrl, alt: '원본 사진' }) : h('div', { class: 'thumb stripe' }),
        h('div', { class: 'confirm-meta' },
          h('span', { class: 'muted' }, 'AI가 알아본 정보'),
          h('div', { class: 'chips' }, tags.map((t) => h('span', { class: 'tag' }, t))),
          d.myAnswer ? h('span', { class: 'muted' }, `${ctx.words.myAnswer}: `, h('b', {}, d.myAnswer)) : null)),
      h('section', { class: 'card' },
        h('div', { class: 'row-between' }, h('span', { class: 'label' }, 'AI가 옮긴 문제'), toggle),
        box, choices),
      h('p', { class: 'muted hint' }, icon('info', 16), '숫자·부호 하나만 달라도 풀이가 틀려져요. 사진과 한 번만 비교해 주세요.'),
      status,
      cta));
}
