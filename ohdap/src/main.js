import { loadCaps, safeLocalStorage } from './caps.js';
import { createStore } from './store.js';
import { isElementary, words } from './grades.js';
import { route, matchRoute, currentPath } from './ui/router.js';
import { h, toast } from './ui/dom.js';
import { renderOnboard } from './ui/screens/onboard.js';
import { renderSettings } from './ui/screens/settings.js';
import { renderHome } from './ui/screens/home.js';

export const APP_VERSION = '0.1.0';

function registerRoutes() {
  route('/onboard', renderOnboard);
  route('/home', renderHome);
  route('/settings', renderSettings);
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
  const ctx = {
    caps, store, ls, settings, draft: null, version: APP_VERSION,
    get words() { return words(ctx.settings.grade); },
    go(path) { if (currentPath() === path) render(); else location.hash = '#' + path; },
    refresh() { render(); },
    onLeave(fn) { cleanup = fn; },
    async saveSettings(s) { await store.saveSettings(s); ctx.settings = s; },
    fail(e) { console.warn(e); toast('저장하지 못했어요. 잠시 뒤 다시 해 주세요.'); }
  };

  async function render() {
    if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
    let path = currentPath();
    if (!ctx.settings.onboarded && path !== '/onboard') { location.hash = '#/onboard'; return; }
    document.body.classList.toggle('kid', isElementary(ctx.settings.grade));
    const m = matchRoute(path) || matchRoute('/home');
    let el;
    try { el = await m.render(ctx, m.params); }
    catch (e) { console.warn(e); el = h('div', { class: 'screen page' }, h('p', {}, '화면을 그리지 못했어요.'), h('a', { href: '#/home', class: 'btn' }, '처음으로')); }
    root.textContent = '';
    root.appendChild(el);
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', render);
  render();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
