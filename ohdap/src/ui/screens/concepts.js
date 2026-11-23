import { h } from '../dom.js';
import { topbar } from '../chrome.js';
import { groupConcepts } from '../../concepts.js';

export async function renderConcepts(ctx) {
  const notes = (await ctx.store.listNotes()).filter((n) => n.status === 'ready');
  const groups = groupConcepts(notes);
  return h('div', { class: 'screen' },
    topbar(ctx.words.concepts + ' 모아 보기', '/home'),
    h('main', { class: 'page' },
      groups.length ? groups.map((g) => {
        const c = (g.notes[0].result.concepts || []).find((x) => x.name.replace(/\s+/g, '').toLowerCase() === g.name.replace(/\s+/g, '').toLowerCase()) || {};
        return h('details', { class: 'card concept-group' },
          h('summary', {}, h('span', { class: 'concept-name' }, g.name), h('span', { class: 'badge' }, `노트 ${g.notes.length}`)),
          c.summary ? h('p', { class: 'concept-sum' }, c.summary) : null,
          h('ul', { class: 'concept-notes' }, g.notes.map((n) => h('li', {}, h('a', { href: '#/note/' + n.id }, String(n.problem).replace(/\$/g, '').slice(0, 50))))));
      }) : h('p', { class: 'muted' }, '아직 정리된 개념이 없어요.')));
}
