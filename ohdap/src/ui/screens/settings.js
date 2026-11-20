import { h } from '../dom.js';
import { GRADES } from '../../grades.js';
import { tabbar, topbar } from '../chrome.js';
import { notesToMarkdown } from '../../export.js';
import { todayYmd } from '../../review.js';
import { toast } from '../dom.js';

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
      ctx.caps.downloads ? h('section', { class: 'card' },
        h('h2', { class: 'h2' }, '내보내기'),
        h('p', { class: 'muted' }, '노트 전체를 글 파일(.md)로 저장해요.'),
        h('button', { class: 'btn', onclick: async () => {
          try {
            const notes = await ctx.store.listNotes();
            await ctx.caps.downloads.save({ filename: `오답노트-${todayYmd()}.md`, data: notesToMarkdown(notes) });
          } catch (e) { if (!(e && (e.code === 'dismissed' || e.code === 'declined'))) toast('저장하지 못했어요.'); }
        } }, '노트 내보내기')) : null,
      h('p', { class: 'muted center' }, `오답노트 ${ctx.version}`)),
    tabbar(ctx, 'settings'));
}
