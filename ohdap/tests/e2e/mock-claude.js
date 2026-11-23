// Injected before the app loads. window.__mockConfig: { name: namespace } or null (no window.claude).
(function () {
  const cfg = window.__mockConfig;
  if (cfg === null || cfg === undefined) return;
  window.claude = { use(name) { return Promise.resolve(cfg[name] === undefined ? null : cfg[name]); } };
})();
