import { h } from './dom.js';

export function aiErrorText(e) {
  const code = e && e.code;
  if (code === 'not_granted' || code === 'unavailable') return 'AI 사용을 허용해야 정리할 수 있어요. 화면 위 권한 메뉴에서 허용해 주세요.';
  if (code === 'rate_limited') return 'AI가 지금 바빠요. 잠시 뒤 다시 해 주세요.';
  if (code === 'bad_output') return 'AI가 답을 제대로 만들지 못했어요. 다시 해 볼까요?';
  if (code === 'unreadable') return '사진을 읽지 못했어요.';
  return '문제가 생겼어요. 잠시 뒤 다시 해 주세요.';
}

export function noAiNotice() {
  return h('div', { class: 'notice' }, 'AI 사용을 허용해야 정리할 수 있어요. 저장된 노트는 그대로 볼 수 있어요.');
}

export function progressSteps(active) {
  const step = (n, label) => h('li', { class: 'pstep' + (n < active ? ' done' : n === active ? ' on' : '') }, h('span', { class: 'pnum' }, n < active ? '✓' : String(n)), label);
  return h('ol', { class: 'psteps', 'aria-label': '진행 단계' }, step(1, '문제 읽기'), h('li', { class: 'pline', 'aria-hidden': 'true' }), step(2, '풀이 만들기'));
}
