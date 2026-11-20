import { h } from '../dom.js';
import { GRADES } from '../../grades.js';
import { tabbar, topbar } from '../chrome.js';

export function renderSettings(ctx) {
  const options = ['auto'].concat(GRADES);
  const btns = options.map((g) => h('button', {
    type: 'button', class: 'grade-btn' + (ctx.settings.grade === g ? ' on' : ''), 'aria-pressed': ctx.settings.grade === g ? 'true' : 'false',
    onclick: async () => {
      try { await ctx.saveSettings(Object.assign({}, ctx.settings, { grade: g })); ctx.refresh(); } catch (e) { ctx.fail(e); }
    }
  }, g === 'auto' ? '자동' : g));
  const where = ctx.store.kind === 'account' ? '계정에 저장' : '이 기기에만 저장됨';
  return h('div', { class: 'screen has-tabbar' },
    topbar('설정', '/home'),
    h('main', { class: 'page' },
      h('section', { class: 'card' },
        h('h2', { class: 'h2' }, '학년'),
        h('p', { class: 'muted' }, '학년에 맞춰 풀이 방법과 말투를 바꿔요. 자동이면 문제를 보고 정해요.'),
        h('div', { class: 'grid4' }, btns)),
      h('section', { class: 'card' },
        h('h2', { class: 'h2' }, '저장 위치'),
        h('p', { class: 'store-where' }, where),
        ctx.store.kind === 'device' ? h('p', { class: 'muted' }, 'claude.ai에 로그인하면 계정에 저장할 수 있어요.') : null),
      h('div', { id: 'export-slot' }),
      h('p', { class: 'muted center' }, `오답노트 ${ctx.version}`)),
    tabbar(ctx, 'settings'));
}
