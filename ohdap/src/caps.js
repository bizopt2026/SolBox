export const CAP_NAMES = ['sample', 'db', 'user', 'assets', 'tts', 'downloads'];

export async function loadCaps(timeoutMs = 10000) {
  const out = {};
  const c = typeof window !== 'undefined' ? window.claude : null;
  await Promise.all(CAP_NAMES.map(async (name) => {
    out[name] = null;
    if (!c || typeof c.use !== 'function') return;
    let timer;
    try {
      out[name] = await Promise.race([
        Promise.resolve(c.use(name)),
        new Promise((r) => { timer = setTimeout(() => r(null), timeoutMs); })
      ]) || null;
    } catch (e) {
      out[name] = null;
    } finally { clearTimeout(timer); }
  }));
  return out;
}

export function safeLocalStorage() {
  try {
    const ls = window.localStorage;
    const k = '__ohdap_probe';
    ls.setItem(k, '1'); ls.removeItem(k);
    return {
      getItem: (key) => { try { return ls.getItem(key); } catch (e) { return null; } },
      setItem: (key, v) => { try { ls.setItem(key, v); } catch (e) { /* full or blocked */ } },
      removeItem: (key) => { try { ls.removeItem(key); } catch (e) { /* ignore */ } }
    };
  } catch (e) {
    const m = new Map();
    return { getItem: (key) => (m.has(key) ? m.get(key) : null), setItem: (key, v) => m.set(key, String(v)), removeItem: (key) => m.delete(key) };
  }
}
