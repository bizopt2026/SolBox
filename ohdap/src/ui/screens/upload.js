import { h, icon } from '../dom.js';
import { topbar } from '../chrome.js';
import { resizeImage } from '../../image.js';
import { readPrompt, validateRead, callJson } from '../../ai.js';
import { aiErrorText, noAiNotice, progressSteps } from '../aimsg.js';

const SUBJECTS = ['수학', '영어', '국어', '과학', '사회'];

export function renderUpload(ctx) {
  const w = ctx.words;
  const st = { photo: null, photoUrl: null, subject: '', busy: false };
  const fileCam = h('input', { type: 'file', accept: 'image/*', capture: 'environment', 'data-src': 'camera', class: 'sr-only', tabindex: '-1' });
  const fileAlbum = h('input', { type: 'file', accept: 'image/*', 'data-src': 'album', class: 'sr-only', tabindex: '-1' });
  const pickArea = h('div');
  const msg = h('div', { class: 'msg', role: 'status' });
  const choose = h('div', { class: 'choose' });
  const myAnswer = h('input', { class: 'input', placeholder: '예: 3', 'aria-label': '내가 쓴 답' });
  const memo = h('input', { class: 'input', placeholder: '예: 시험에서 시간 부족', 'aria-label': '메모' });
  const go = h('button', { class: 'btn primary big', onclick: submit, disabled: true }, icon('sparkle', 20), 'AI로 정리하기');

  async function onFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    msg.textContent = '';
    choose.textContent = '';
    try {
      st.photo = await resizeImage(f);
    } catch (err) {
      msg.textContent = '이 사진은 열 수 없어요. 다른 사진으로 해 주세요.';
      return;
    }
    if (st.photoUrl) URL.revokeObjectURL(st.photoUrl);
    st.photoUrl = URL.createObjectURL(st.photo);
    paintPick();
  }
  fileCam.addEventListener('change', onFile);
  fileAlbum.addEventListener('change', onFile);

  function clearPhoto() {
    st.photo = null; msg.textContent = ''; choose.textContent = '';
    fileCam.value = ''; fileAlbum.value = '';
    paintPick();
  }

  function paintPick() {
    pickArea.textContent = '';
    if (!st.photo) {
      pickArea.appendChild(h('div', { class: 'grid2' },
        h('button', { type: 'button', class: 'pick pick-cam', onclick: () => fileCam.click() }, icon('camera', 40), '사진 찍기'),
        h('button', { type: 'button', class: 'pick pick-album', onclick: () => fileAlbum.click() }, icon('album', 40), '앨범에서 고르기')));
      pickArea.appendChild(h('p', { class: 'muted' }, '문제 하나가 화면에 꽉 차게, 그림자 없이 찍으면 더 정확하게 읽어요.'));
    } else {
      pickArea.appendChild(h('div', { class: 'preview' },
        h('img', { src: st.photoUrl, alt: '올린 문제 사진' }),
        h('button', { type: 'button', class: 'retake', onclick: clearPhoto }, '다시 찍기')));
    }
    go.disabled = !st.photo || st.busy;
  }

  const chips = SUBJECTS.map((s) => h('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', onclick: () => {
    st.subject = st.subject === s ? '' : s;
    chips.forEach((c) => { const on = c.textContent === st.subject; c.classList.toggle('on', on); c.setAttribute('aria-pressed', on ? 'true' : 'false'); });
  } }, s));

  function toConfirm(problem) {
    ctx.draft = { photo: st.photo, photoUrl: st.photoUrl, subject: problem.subject || st.subject, myAnswer: myAnswer.value.trim(), memo: memo.value.trim(), read: problem };
    ctx.go('/confirm');
  }

  async function submit() {
    if (!st.photo || st.busy) return;
    st.busy = true; go.disabled = true;
    msg.textContent = ''; choose.textContent = '';
    msg.appendChild(progressSteps(1));
    try {
      const res = await callJson(ctx.caps.sample, readPrompt({ grade: ctx.settings.grade, subjectHint: st.subject, myAnswer: myAnswer.value.trim() }), { images: [st.photo], modelTier: 'complex', validate: validateRead });
      msg.textContent = '';
      if (!res.readable) {
        msg.appendChild(h('div', { class: 'notice warn' }, res.reason || '사진에서 문제를 찾지 못했어요.', ' 다시 찍어 주세요.'));
      } else if (res.problems.length === 1) {
        toConfirm(res.problems[0]);
        return;
      } else {
        choose.appendChild(h('p', { class: 'label' }, '어떤 문제를 정리할까요?'));
        res.problems.forEach((p, i) => choose.appendChild(h('button', { type: 'button', class: 'btn choice', onclick: () => toConfirm(p) }, `${i + 1}. ${p.problem.replace(/\$/g, '').slice(0, 60)}`)));
      }
    } catch (e) {
      msg.textContent = '';
      msg.appendChild(h('div', { class: 'notice warn' }, aiErrorText(e)));
    }
    st.busy = false; go.disabled = !st.photo;
  }

  paintPick();
  const hasAi = !!ctx.caps.sample;
  const ctaSlot = h('div', { class: 'cta-slot' }, hasAi ? go : noAiNotice());
  if (hasAi && typeof ctx.caps.sample.limits === 'function') {
    ctx.caps.sample.limits().then((l) => {
      if (l && !l.images) { ctaSlot.textContent = ''; ctaSlot.appendChild(h('div', { class: 'notice warn' }, aiErrorText({ code: 'images_unavailable' }))); }
    }).catch(() => {});
  }
  return h('div', { class: 'screen' },
    topbar(w.upload, '/home'),
    h('main', { class: 'page' },
      fileCam, fileAlbum, pickArea,
      h('div', { class: 'field' }, h('span', {}, '과목 ', h('small', {}, '· 비우면 AI가 알아봐요')), h('div', { class: 'chips' }, chips)),
      h('label', { class: 'field' }, h('span', {}, w.myAnswer, ' ', h('small', {}, '· 적으면 어디서 틀렸는지 짚어 줘요')), myAnswer),
      h('label', { class: 'field' }, h('span', {}, '메모 ', h('small', {}, '· 선택')), memo),
      msg, choose,
      ctaSlot));
}
