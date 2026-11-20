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

export function currentPath() {
  const p = (location.hash || '').replace(/^#/, '');
  return p || '/home';
}
