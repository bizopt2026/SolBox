import { loadCaps, safeLocalStorage } from './caps.js';
import { createStore } from './store.js';
import { isElementary, words } from './grades.js';
import { route, matchRoute, hashPath } from './ui/router.js';
import { h, toast } from './ui/dom.js';
import { renderOnboard } from './ui/screens/onboard.js';
import { renderSettings } from './ui/screens/settings.js';
import { renderHome } from './ui/screens/home.js';
import { renderUpload } from './ui/screens/upload.js';
import { renderConfirm } from './ui/screens/confirm.js';
import { renderNote } from './ui/screens/note.js';
import { solveDraft } from './ui/solve.js';
import { renderBoard } from './ui/screens/board.js';
import { renderReview } from './ui/screens/review.js';
import { renderDrill } from './ui/screens/drill.js';
import { renderConcepts } from './ui/screens/concepts.js';

export const APP_VERSION = '0.1.1';

function registerRoutes() {
  route('/onboard', renderOnboard);
  route('/home', renderHome);
  route('/settings', renderSettings);
  route('/upload', renderUpload);
  route('/confirm', renderConfirm);
  route('/note/:id', renderNote);
  route('/board/:id/:sol', renderBoard);
  route('/review', renderReview);
  route('/drill/:id', renderDrill);
  route('/drill/:id/:drillId', renderDrill);
  route('/concepts', renderConcepts);
}

export async function boot() {
  const root = document.getElementById('app');
  root.textContent = '';
  root.appendChild(h('p', { class: 'loading' }, '불러오는 중…'));
  registerRoutes();
  const caps = await loadCaps();
  const ls = safeLocalStorage();
  let store;
  try { store = await createStore({ db: caps.db, user: caps.user, localStorage: ls }); }
  catch (e) { store = await createStore({ db: null, user: null, localStorage: ls }); }
  let settings;
  try { settings = await store.getSettings(); }
  catch (e) { store = await createStore({ db: null, user: null, localStorage: ls }); settings = await store.getSettings(); }

  let cleanup = null;
  let renderSeq = 0;
  let path = hashPath() || '/home';
  const ctx = {
    caps, store, ls, settings, draft: null, version: APP_VERSION, solveDraft,
    get words() { return words(ctx.settings.grade); },
    go(p) { path = p; render(); },
    refresh() { render(); },
    // Screens pass the seq they were rendered for; a late registration from a stale screen runs at once.
    onLeave(fn, seq) { if (seq !== undefined && seq !== renderSeq) { try { fn(); } catch (e) { /* ignore */ } return; } cleanup = fn; },
    get renderSeq() { return renderSeq; },
    async saveSettings(s) { await store.saveSettings(s); ctx.settings = s; },
    fail(e) { console.warn(e); toast('저장하지 못했어요. 잠시 뒤 다시 해 주세요.'); }
  };

  async function render() {
    if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
    const seq = ++renderSeq;
    if (!ctx.settings.onboarded && path !== '/onboard') path = '/onboard';
    document.body.classList.toggle('kid', isElementary(ctx.settings.grade));
    const m = matchRoute(path) || matchRoute('/home');
    let el;
    try { el = await m.render(ctx, m.params); }
    catch (e) { console.warn(e); el = h('div', { class: 'screen page' }, h('p', {}, '화면을 그리지 못했어요.'), h('a', { href: '#/home', class: 'btn' }, '처음으로')); }
    if (seq !== renderSeq) return; // a newer screen was asked for while this one loaded
    root.textContent = '';
    root.appendChild(el);
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', () => { const p = hashPath(); if (p) { path = p; render(); } });
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href^="#/"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    ctx.go(a.getAttribute('href').slice(1));
  });
  render();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
