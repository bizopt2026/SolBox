import { h, icon, toast } from '../dom.js';
import { tabbar } from '../chrome.js';
import { isElementary } from '../../grades.js';
import { dueNotes, todayYmd, stageLabel, MASTERED } from '../../review.js';
import { filterNotes } from '../../export.js';
import { deviceNoteCount, migrateDeviceToAccount } from '../../store.js';

function noteTitle(n) {
  return String(n.problem || '').replace(/\$/g, '').split('\n')[0].slice(0, 80) || '문제';
}

function badgeClass(n, today) {
  if (n.stage >= MASTERED) return 'badge done';
  if (n.status !== 'ready') return 'badge';
  return n.due && n.due <= today ? 'badge due' : 'badge';
}

function noteItem(n, today) {
  return h('a', { class: 'note-item', href: '#/note/' + n.id },
    n.photoId ? h('img', { class: 'nthumb', src: '/_blob/' + n.photoId, alt: '', loading: 'lazy' }) : h('span', { class: 'nthumb stripe', 'aria-hidden': 'true' }, n.subject ? n.subject.slice(0, 2) : ''),
    h('span', { class: 'nbody' },
      h('span', { class: 'muted small' }, [n.subject, n.unit].filter(Boolean).join(' · ')),
      h('span', { class: 'ntitle' }, noteTitle(n)),
      h('span', { class: badgeClass(n, today) }, n.status !== 'ready' ? '정리 필요' : stageLabel(n, today))),
    n.starred ? h('span', { class: 'nstar', 'aria-label': '다시보기 표시' }, icon('star', 16)) : null);
}

function migrateBanner(ctx) {
  if (ctx.store.kind !== 'account') return null;
  const count = deviceNoteCount(ctx.ls);
  if (!count) return null;
  const btn = h('button', { class: 'btn primary small-btn', onclick: async () => {
    btn.disabled = true;
    try {
      const r = await migrateDeviceToAccount(ctx.ls, ctx.store);
      ctx.settings = await ctx.store.getSettings();
      toast(`노트 ${r.moved}개를 계정으로 옮겼어요.`);
    } catch (e) { ctx.fail(e); }
    ctx.refresh();
  } }, '계정으로 옮기기');
  return h('div', { class: 'notice migrate' }, h('span', {}, `이 기기에 노트 ${count}개가 있어요.`), btn);
}

export async function renderHome(ctx) {
  const kid = isElementary(ctx.settings.grade);
  const w = ctx.words;
  const today = todayYmd();
  const notes = await ctx.store.listNotes();
  const due = dueNotes(notes.filter((n) => n.status === 'ready'), today);
  const list = h('div', { class: 'note-list' });
  const st = { subject: '전체', query: '', byUnit: false };

  function paintList() {
    list.textContent = '';
    const shown = filterNotes(notes, { subject: st.subject, query: st.query });
    if (!notes.length) {
      list.appendChild(h('div', { class: 'empty' }, h('p', {}, '아직 노트가 없어요.'), h('p', { class: 'muted' }, kid ? '틀린 문제를 찍어서 올려 보세요.' : '틀린 문제 사진을 올리면 정답과 풀이를 정리해 줘요.')));
      return;
    }
    if (!shown.length) { list.appendChild(h('p', { class: 'muted' }, '찾는 노트가 없어요.')); return; }
    if (st.byUnit && !kid) {
      const groups = new Map();
      for (const n of shown) { const k = [n.subject, n.unit].filter(Boolean).join(' · ') || '기타'; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(n); }
      for (const [k, ns] of groups) list.append(h('h3', { class: 'group-title' }, `${k} (${ns.length})`), ...ns.map((n) => noteItem(n, today)));
    } else shown.forEach((n) => list.appendChild(noteItem(n, today)));
  }

  const head = h('header', { class: 'home-head' }, h('h1', { class: 'h-big' }, '오답노트'),
    kid ? h('span', { class: 'stars' }, icon('star', 20), `별 ${ctx.settings.stars || 0}개`)
      : h('a', { class: 'iconbtn', href: '#/settings', 'aria-label': '설정' }, icon('gear')));

  const top = [];
  const banner = migrateBanner(ctx);
  if (banner) top.push(banner);
  if (ctx.store.kind === 'device') top.push(h('p', { class: 'device-note' }, '이 기기에만 저장됨'));

  if (kid) {
    top.push(h('a', { class: 'kid-shoot', href: '#/upload' }, h('span', { class: 'kid-shoot-ic' }, icon('camera', 40)),
      h('span', { class: 'kid-shoot-text' }, h('b', {}, w.upload), h('span', {}, '사진을 찍으면 풀이를 알려 줘요'))));
    if (due.length) top.push(h('a', { class: 'kid-challenge', href: '#/drill/' + due[0].id },
      h('span', { class: 'row-between' }, h('b', {}, `오늘의 도전 ${due.length}문제`), h('span', { class: 'pill-dark' }, '도전!')),
      h('span', { class: 'muted' }, '맞히면 별을 받아요')));
    top.push(h('h2', { class: 'h2' }, '내 오답노트'));
  } else {
    if (due.length) top.push(h('a', { class: 'today-card', href: '#/review' },
      h('span', { class: 'today-card-text' }, h('span', { class: 'small' }, '오늘의 복습'), h('b', {}, `다시 풀 문제 ${due.length}개`)),
      h('span', { class: 'pill-marker' }, '시작')));
    const search = h('input', { class: 'search-input', placeholder: '문제, 개념, 단원 검색', 'aria-label': '검색', oninput: (e) => { st.query = e.target.value; paintList(); } });
    top.push(h('label', { class: 'search' }, icon('search', 18), search));
    const subjects = ['전체'].concat([...new Set(notes.map((n) => n.subject).filter(Boolean))]);
    const chips = subjects.map((s) => h('button', { type: 'button', class: 'chip' + (s === '전체' ? ' on' : ''), 'aria-pressed': s === '전체' ? 'true' : 'false', onclick: () => {
      st.subject = s;
      chips.forEach((c) => { const on = c.textContent === s; c.classList.toggle('on', on); c.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      paintList();
    } }, s));
    if (notes.length) top.push(h('div', { class: 'chips' }, chips));
    const unitBtn = h('button', { class: 'linkish', type: 'button', 'aria-pressed': 'false', onclick: () => { st.byUnit = !st.byUnit; unitBtn.textContent = st.byUnit ? '최근 순 보기' : '단원별 보기'; unitBtn.setAttribute('aria-pressed', st.byUnit ? 'true' : 'false'); paintList(); } }, '단원별 보기');
    top.push(h('div', { class: 'row-between' }, h('h2', { class: 'h3' }, '최근 노트'),
      notes.length ? h('span', { class: 'row-gap' }, h('a', { class: 'linkish', href: '#/concepts' }, '개념 모아 보기'), unitBtn) : null));
  }
  paintList();
  return h('div', { class: 'screen has-tabbar' }, head, h('main', { class: 'page' }, top, list), tabbar(ctx, 'home'));
}
