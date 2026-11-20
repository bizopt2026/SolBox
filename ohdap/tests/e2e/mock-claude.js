// Injected before the app loads. window.__mockConfig: { name: namespace } or null (no window.claude).
(function () {
  const cfg = window.__mockConfig;
  if (cfg === null || cfg === undefined) return;
  window.claude = {
    use(name) {
      const v = typeof cfg[name] === 'function' && cfg[name].length === 0 && name !== 'sample' ? cfg[name]() : cfg[name];
      return Promise.resolve(v === undefined ? null : v);
    }
  };
})();
