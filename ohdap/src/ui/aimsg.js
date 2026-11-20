import { h } from './dom.js';

const AI_TEXT = {
  not_granted: 'AI 사용을 허용해야 정리할 수 있어요. 화면 위 권한 메뉴에서 허용해 주세요.',
  unavailable: 'AI 사용을 허용해야 정리할 수 있어요. 화면 위 권한 메뉴에서 허용해 주세요.',
  sampling_disabled: '이 계정에서는 AI를 쓸 수 없어요.',
  capability_disabled: '이 화면에서는 AI를 쓸 수 없어요. claude.ai에서 열어 주세요.',
  rate_limited: 'AI가 지금 바빠요. 잠시 뒤 다시 해 주세요.',
  queue_overflow: 'AI가 지금 바빠요. 잠시 뒤 다시 해 주세요.',
  upstream_error: 'AI에 연결하지 못했어요. 잠시 뒤 다시 해 주세요.',
  image_rejected: '이 사진은 AI가 볼 수 없어요. 다른 사진으로 해 보세요.',
  images_unavailable: '이 화면에서는 사진을 AI에 보낼 수 없어요. claude.ai 웹이나 앱에서 열어 주세요.',
  refused: 'AI가 이 문제는 정리하지 않았어요. 문제 글을 확인해 주세요.',
  prompt_too_large: '문제가 너무 길어요. 줄여서 다시 해 주세요.',
  session_expired: 'claude.ai에 다시 로그인한 뒤 해 주세요.',
  bad_output: 'AI가 답을 제대로 만들지 못했어요. 다시 해 볼까요?',
  unreadable: '사진을 읽지 못했어요.'
};

export function aiErrorText(e) {
  return AI_TEXT[e && e.code] || '문제가 생겼어요. 잠시 뒤 다시 해 주세요.';
}

export function noAiNotice() {
  return h('div', { class: 'notice' }, 'AI 사용을 허용해야 정리할 수 있어요. 저장된 노트는 그대로 볼 수 있어요.');
}

export function progressSteps(active) {
  const step = (n, label) => h('li', { class: 'pstep' + (n < active ? ' done' : n === active ? ' on' : '') }, h('span', { class: 'pnum' }, n < active ? '✓' : String(n)), label);
  return h('ol', { class: 'psteps', 'aria-label': '진행 단계' }, step(1, '문제 읽기'), h('li', { class: 'pline', 'aria-hidden': 'true' }), step(2, '풀이 만들기'));
}
