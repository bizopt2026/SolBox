const ROUTES = [];

export function route(pattern, render) {
  const keys = [];
  const re = new RegExp('^' + pattern.replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '$');
  ROUTES.push({ re, keys, render });
}

export function matchRoute(path) {
  for (const r of ROUTES) {
    const m = path.match(r.re);
    if (m) {
      const params = {};
      r.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
      return { render: r.render, params };
    }
  }
  return null;
}

// Paths look like '/note/n_1'. The artifact viewer does not pass '#/path' hashes, so the app keeps
// its own current path and never writes location.hash; an incoming '#/path' (tests, local file) is read.
export function hashPath() {
  const p = (location.hash || '').replace(/^#/, '');
  return p.startsWith('/') ? p : '';
}
