const plain = (s) => String(s || '').replace(/\$/g, '');

export function filterNotes(notes, { subject, query, onlyOpen, onlyStarred } = {}) {
  const q = String(query || '').trim().toLowerCase();
  return notes.filter((n) => {
    if (subject && subject !== '전체' && n.subject !== subject) return false;
    if (onlyOpen && n.stage >= 4) return false;
    if (onlyStarred && !n.starred) return false;
    if (q) {
      const hay = [n.problem, n.unit, n.subject, ...(n.tags || []), ...((n.result && n.result.concepts) || []).map((c) => c.name)].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function notesToMarkdown(notes) {
  const bySubject = new Map();
  for (const n of notes) {
    const s = n.subject || '기타';
    if (!bySubject.has(s)) bySubject.set(s, []);
    bySubject.get(s).push(n);
  }
  const out = ['# 오답노트', ''];
  for (const [subject, list] of bySubject) {
    out.push(`## ${subject}`, '');
    for (const n of list) {
      out.push(`### ${n.unit || '단원 없음'} — ${plain(n.problem).split('\n')[0].slice(0, 60)}`, '');
      out.push(`- 문제: ${plain(n.problem)}`);
      if (n.myAnswer) out.push(`- 내가 쓴 답: ${plain(n.myAnswer)}`);
      if (n.result) {
        out.push(`- 정답: ${plain(n.result.answer)}`);
        const cs = (n.result.concepts || []).map((c) => c.name);
        if (cs.length) out.push(`- 핵심 개념: ${cs.join(', ')}`);
      } else out.push('- 아직 정리하지 못했어요');
      out.push('');
    }
  }
  return out.join('\n');
}
