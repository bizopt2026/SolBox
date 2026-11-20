export const APP_VERSION = '0.1.0';

export function boot() {
  const root = document.getElementById('app');
  root.textContent = '';
  const h = document.createElement('h1');
  h.textContent = '오답노트';
  root.appendChild(h);
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
}
