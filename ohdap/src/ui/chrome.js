import { h, icon } from './dom.js';
import { isElementary } from '../grades.js';

export function tabbar(ctx, active) {
  const kid = isElementary(ctx.settings.grade);
  const item = (key, path, label, ic) => h('a', { href: '#' + path, class: 'tab' + (active === key ? ' on' : ''), 'aria-current': active === key ? 'page' : null }, icon(ic, kid ? 28 : 24), label);
  const plus = h('a', { href: '#/upload', class: 'tab-plus', 'aria-label': '문제 올리기' }, icon('plus', 26));
  return h('nav', { class: 'tabbar' }, kid
    ? [item('home', '/home', '오답노트', 'note'), item('review', '/review', '다시 풀기', 'review'), item('settings', '/settings', '나', 'person')]
    : [item('home', '/home', '노트', 'note'), plus, item('review', '/review', '복습', 'review')]);
}

export function topbar(title, backPath, right) {
  return h('header', { class: 'top' },
    backPath ? h('a', { href: '#' + backPath, class: 'iconbtn', 'aria-label': '뒤로' }, icon('back')) : h('span', { class: 'iconbtn-space' }),
    h('h1', { class: 'top-title' }, title),
    right || h('span', { class: 'iconbtn-space' }));
}

export function screen(...kids) {
  return h('div', { class: 'screen' }, ...kids);
}
