import { h, icon } from '../dom.js';
import { GRADES } from '../../grades.js';

export function renderOnboard(ctx) {
  let picked = ctx.settings.grade && ctx.settings.grade !== 'auto' ? ctx.settings.grade : '초3';
  const btns = new Map();
  const startBtn = h('button', { class: 'btn primary big', onclick: start }, '');
  function paint() {
    for (const [g, b] of btns) { b.classList.toggle('on', g === picked); b.setAttribute('aria-pressed', g === picked ? 'true' : 'false'); }
    startBtn.textContent = `${picked} · 시작하기`;
  }
  async function start() {
    startBtn.disabled = true;
    const s = Object.assign({}, ctx.settings, { grade: picked, onboarded: true });
    try { await ctx.saveSettings(s); ctx.go('/home'); } catch (e) { startBtn.disabled = false; ctx.fail(e); }
  }
  const gradeBtn = (g) => { const b = h('button', { class: 'grade-btn', type: 'button', onclick: () => { picked = g; paint(); } }, g); btns.set(g, b); return b; };
  const el = h('div', { class: 'screen onboard' },
    h('div', { class: 'logo', 'aria-hidden': 'true' }, icon('check', 48)),
    h('h1', { class: 'h-big' }, '몇 학년이에요?'),
    h('p', { class: 'lead' }, '학년에 맞는 쉬운 말로 설명해 줄게요. 나중에 설정에서 바꿀 수 있어요.'),
    h('div', { class: 'label' }, '초등학교'),
    h('div', { class: 'grid4' }, GRADES.slice(0, 4).map(gradeBtn)),
    h('div', { class: 'label' }, '중학교 · 고등학교'),
    h('div', { class: 'grid3' }, GRADES.slice(4).map(gradeBtn)),
    h('div', { class: 'bottom-cta' }, startBtn));
  paint();
  return el;
}
