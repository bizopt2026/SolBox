import { h } from './dom.js';
import { columnModel } from '../column.js';

const OP_SIGN = { '+': '+', '-': '−', '×': '×' };

// opts: { marks: bool, result: bool, focus: bool }
export function renderColumn(v, opts = {}) {
  const m = columnModel(v);
  const cols = [];
  cols.push(h('div', { class: 'col' }, h('span', { class: 'col-mark' }), h('span', { class: 'col-a' }), h('span', { class: 'col-b' }, OP_SIGN[m.op] || m.op), h('span', { class: 'col-line' }), h('span', { class: 'col-result' })));
  for (let i = 0; i < m.width; i++) {
    const mark = opts.marks ? m.rows.marks[i] : '';
    cols.push(h('div', { class: 'col' + (opts.focus && m.rows.focus[i] ? ' focus' : '') },
      h('span', { class: 'col-mark' }, mark),
      h('span', { class: 'col-a' + (mark ? ' struck' : '') }, m.rows.a[i]),
      h('span', { class: 'col-b' }, m.rows.b[i]),
      h('span', { class: 'col-line' }),
      h('span', { class: 'col-result' }, opts.result ? m.rows.result[i] : '')));
  }
  const label = `${v.a} ${OP_SIGN[m.op] || m.op} ${v.b} 세로셈`;
  return h('div', { class: 'column-calc', role: 'img', 'aria-label': label }, cols);
}
