import { h, icon } from '../dom.js';
import { renderMathText } from '../../mathtext.js';
import { createSpeaker, browserSynth } from '../../speaker.js';
import { renderColumn } from '../columnview.js';

const RATES = [0.8, 1, 1.25, 1.5];

export async function renderBoard(ctx, params) {
  const mySeq = ctx.renderSeq;
  let solutions, backPath, problem;
  if (params.sol === 'drill' && ctx.boardDrill) {
    solutions = [{ title: ctx.boardDrill.title || '풀이', steps: ctx.boardDrill.steps }];
    backPath = ctx.boardDrill.back || '/home';
    problem = ctx.boardDrill.problem || '';
  } else {
    const note = await ctx.store.getNote(params.id);
    if (!note || !note.result) { setTimeout(() => ctx.go('/home'), 0); return h('div', { class: 'screen' }); }
    solutions = note.result.solutions;
    backPath = '/note/' + note.id;
    problem = note.problem;
  }
  const solIdx = Math.min(Math.max(0, Number(params.sol) || 0), solutions.length - 1);
  const sol = solutions[solIdx];
  const steps = sol.steps.length ? sol.steps : [{ board: '', say: '' }];
  const says = steps.map((s) => s.say || s.board.replace(/\$/g, ''));
  const st = { idx: 0, started: false, playing: false, rate: 1 };

  const sp = createSpeaker({ tts: ctx.caps.tts, synth: browserSynth() });
  ctx.onLeave(() => sp.stop(), mySeq);

  const pos = h('span', { class: 'board-pos' });
  const bars = h('div', { class: 'board-bars', 'aria-hidden': 'true' });
  const list = h('ol', { class: 'board-steps' });
  const caption = h('p', { class: 'caption', 'aria-live': 'polite' });
  const playBtn = h('button', { class: 'bplay', onclick: togglePlay });
  const rateBtn = h('button', { class: 'bctl rate', 'aria-label': '재생 속도', onclick: () => {
    st.rate = RATES[(RATES.indexOf(st.rate) + 1) % RATES.length];
    sp.setRate(st.rate);
    if (st.playing && sp.mode !== 'tts') sp.play(says, st.idx, st.rate);
    paint();
  } });

  function paint() {
    pos.textContent = `${st.idx + 1}/${steps.length}`;
    bars.textContent = '';
    steps.forEach((_, i) => bars.appendChild(h('span', { class: i <= st.idx ? 'on' : '' })));
    list.textContent = '';
    steps.slice(0, st.idx + 1).forEach((s, i) => {
      const cur = i === st.idx;
      const li = h('li', { class: 'bstep' + (cur ? ' current' : ' past') }, h('span', { class: 'bnum' }, String(i + 1)));
      if (s.visual) li.appendChild(renderColumn(s.visual, { marks: true, result: true, focus: cur }));
      else { const b = h('span', { class: 'btext' }); b.innerHTML = renderMathText(s.board, window.katex); li.appendChild(b); }
      list.appendChild(li);
    });
    const cur = list.lastElementChild;
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
    caption.textContent = says[st.idx];
    playBtn.textContent = '';
    playBtn.appendChild(icon(st.playing ? 'pause' : 'play', 30));
    playBtn.setAttribute('aria-label', st.playing ? '멈추기' : '재생');
    rateBtn.textContent = (st.rate === 1 ? '1.0' : String(st.rate)) + 'x';
  }

  sp.onLine((i) => { st.idx = i; paint(); });
  sp.onEnd(() => { st.playing = false; st.started = false; paint(); });

  function togglePlay() {
    if (st.playing) { sp.pause(); st.playing = false; }
    else if (st.started) { sp.resume(); st.playing = true; }
    else {
      if (st.idx >= steps.length - 1 && steps.length > 1) st.idx = 0;
      st.started = true; st.playing = true;
      sp.play(says, st.idx, st.rate);
    }
    paint();
  }
  function goStep(n) {
    st.idx = Math.max(0, Math.min(steps.length - 1, n));
    if (st.playing) sp.play(says, st.idx, st.rate);
    else if (st.started) { sp.stop(); st.started = false; }
    paint();
  }

  paint();
  const other = solutions.length > 1
    ? h('a', { class: 'bctl', href: `#/board/${params.id}/${(solIdx + 1) % solutions.length}`, 'aria-label': '다른 풀이' }, `풀이 ${((solIdx + 1) % solutions.length) + 1}`)
    : h('span', { class: 'bctl ghost' });
  return h('div', { class: 'screen board-screen' },
    h('header', { class: 'top' },
      h('a', { href: '#' + backPath, class: 'iconbtn', 'aria-label': '닫기' }, icon('close')),
      h('h1', { class: 'top-title' }, solutions.length > 1 ? `풀이 ${solIdx + 1} · ${sol.title}` : sol.title),
      pos),
    bars,
    h('main', { class: 'board-main' },
      problem ? (() => { const p = h('p', { class: 'board-problem' }); p.innerHTML = renderMathText(problem, window.katex); return p; })() : null,
      list),
    h('div', { class: 'caption-box' }, icon('speaker', 20), caption),
    h('div', { class: 'board-ctl' },
      rateBtn,
      h('button', { class: 'bctl round', 'aria-label': '이전 단계', onclick: () => goStep(st.idx - 1) }, icon('prev', 22)),
      playBtn,
      h('button', { class: 'bctl round', 'aria-label': '다음 단계', onclick: () => goStep(st.idx + 1) }, icon('next', 22)),
      other));
}
