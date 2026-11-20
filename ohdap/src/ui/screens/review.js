import { h, icon } from '../dom.js';
import { tabbar } from '../chrome.js';
import { dueNotes, todayYmd, addDays, MASTERED } from '../../review.js';
import { groupConcepts } from '../../concepts.js';
import { isElementary } from '../../grades.js';

export function firstLine(text) {
  return String(text || '').replace(/\$/g, '').split('\n')[0].slice(0, 80);
}

export async function renderReview(ctx) {
  const w = ctx.words;
  const kid = isElementary(ctx.settings.grade);
  const today = todayYmd();
  const notes = (await ctx.store.listNotes()).filter((n) => n.status === 'ready');
  const due = dueNotes(notes, today);
  const doneToday = notes.filter((n) => n.lastReviewed === today && !due.includes(n));
  const items = doneToday.concat(due);
  const total = items.length;
  const finished = doneToday.length;
  const tomorrow = addDays(today, 1), weekEnd = addDays(today, 7);
  const cntTomorrow = notes.filter((n) => n.stage < MASTERED && n.due === tomorrow).length;
  const cntWeek = notes.filter((n) => n.stage < MASTERED && n.due > today && n.due <= weekEnd).length;
  const cntDone = notes.filter((n) => n.stage >= MASTERED).length;
  const top = groupConcepts(notes.filter((n) => n.stage < MASTERED)).slice(0, 5);

  const list = h('ul', { class: 'today-list' }, items.map((n) => h('li', { class: 'today-item' },
    h('a', { href: '#/note/' + n.id, class: 'today-text' },
      h('span', { class: 'muted small' }, [n.subject, n.unit, `${Math.min(n.stage + 1, 5)}단계`].filter(Boolean).join(' · ')),
      h('span', { class: 'today-title' }, firstLine(n.problem))),
    n.lastReviewed === today && !due.includes(n)
      ? h('span', { class: n.lastCorrect ? 'got ok' : 'got bad' }, icon(n.lastCorrect ? 'check' : 'cross', 16), n.lastCorrect ? '맞힘' : '다시')
      : h('a', { class: 'btn primary pill', href: '#/drill/' + n.id }, '풀기'))));

  return h('div', { class: 'screen has-tabbar' },
    h('header', { class: 'home-head' }, h('h1', { class: 'h-big' }, w.review),
      kid ? h('span', { class: 'stars' }, icon('star', 20), `별 ${ctx.settings.stars || 0}개`) : null),
    h('main', { class: 'page' },
      h('section', { class: 'card' },
        h('div', { class: 'row-between' }, h('h2', { class: 'h2' }, `오늘 다시 풀 문제`), h('span', { class: 'muted' }, h('b', {}, String(finished)), ` / ${total} 끝`)),
        h('div', { class: 'progress', 'aria-hidden': 'true' }, h('span', { style: `width:${total ? Math.round((finished / total) * 100) : 0}%` })),
        total ? list : h('p', { class: 'muted' }, notes.length ? '오늘은 다시 풀 문제가 없어요.' : '아직 노트가 없어요. 틀린 문제를 올려 보세요.')),
      h('section', { class: 'sec' },
        h('h2', { class: 'h3' }, `다가오는 ${w.review}`),
        h('div', { class: 'grid3' },
          h('div', { class: 'stat' }, h('span', { class: 'muted small' }, '내일'), h('b', {}, `${cntTomorrow}개`)),
          h('div', { class: 'stat' }, h('span', { class: 'muted small' }, '이번 주'), h('b', {}, `${cntWeek}개`)),
          h('div', { class: 'stat done' }, h('span', { class: 'small' }, '정복'), h('b', {}, `${cntDone}개`))),
        h('p', { class: 'muted' }, '비슷한 문제를 맞히면 1일 → 3일 → 7일 → 14일 → 정복으로 올라가요. 틀리면 1일부터 다시.')),
      top.length ? h('section', { class: 'sec' },
        h('h2', { class: 'h3' }, '자주 틀리는 개념'),
        h('div', { class: 'chips' }, top.map((g) => h('a', { class: 'tag', href: '#/concepts' }, `${g.name} · ${g.notes.length}`)))) : null),
    tabbar(ctx, 'review'));
}
