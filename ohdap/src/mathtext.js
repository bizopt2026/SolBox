export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function splitMath(s) {
  const src = String(s || '');
  const out = [];
  let buf = '';
  let i = 0;
  const pushText = (t) => {
    if (!t) return;
    const last = out[out.length - 1];
    if (last && last.type === 'text') last.value += t; else out.push({ type: 'text', value: t });
  };
  while (i < src.length) {
    const c = src[i];
    if (c === '\\' && src[i + 1] === '$') { buf += '$'; i += 2; continue; }
    if (c === '$') {
      let j = i + 1;
      let found = -1;
      while (j < src.length) {
        if (src[j] === '\\' && src[j + 1] === '$') { j += 2; continue; }
        if (src[j] === '$') { found = j; break; }
        j++;
      }
      if (found > i + 1) {
        pushText(buf); buf = '';
        out.push({ type: 'math', value: src.slice(i + 1, found) });
        i = found + 1;
        continue;
      }
    }
    buf += c; i++;
  }
  pushText(buf);
  return out;
}

export function renderMathText(s, katexLib) {
  return splitMath(s).map((p) => {
    if (p.type === 'text') return escapeHtml(p.value);
    if (katexLib) {
      try { return katexLib.renderToString(p.value, { throwOnError: true }); } catch (e) { /* fall through */ }
    }
    return `<span class="math-raw">${escapeHtml(p.value)}</span>`;
  }).join('');
}
