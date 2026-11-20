import { h } from '../dom.js';
import { tabbar } from '../chrome.js';

export function renderHome(ctx) {
  return h('div', { class: 'screen has-tabbar' },
    h('header', { class: 'home-head' }, h('h1', { class: 'h-big' }, '오답노트')),
    h('main', { class: 'page' },
      ctx.store.kind === 'device' ? h('p', { class: 'device-note' }, '이 기기에만 저장됨') : null),
    tabbar(ctx, 'home'));
}
